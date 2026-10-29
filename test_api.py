"""Testes da API e do envio: dependências em requirements_testes.txt."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from fastapi.testclient import TestClient

from envio_api import Outbox, Publisher
from servidor import create_app


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name)/"api.db"
        self.now = datetime.now(timezone.utc)
        self.client = TestClient(create_app(self.database, clock=lambda:self.now))
        self.client.__enter__()
        self.addCleanup(lambda:self.client.__exit__(None,None,None))
        self.session_id = str(uuid4())

    def event(self, sequence=1, **changes):
        event = {"event_id":str(uuid4()),"session_id":self.session_id,"sequence":sequence,
                 "camera_id":"webcam-01","sala":"Sala de testes","observed_at":self.now.isoformat(),
                 "state":"PRESENCA_DETECTADA","count":1,"score":0.88,"fps":20,"inference_ms":50,
                 "image_age_ms":60,"confidence":0.65,"model_name":"yolo26n.pt","imgsz":320,
                 "reason":"mudanca_estado","mode":"camera","config":{"imgsz":320}}
        event.update(changes)
        return event

    def test_api_panel_and_static_files_are_served(self):
        self.assertEqual(self.client.get('/api/health').json()['status'],'ok')
        self.assertIn('SENTINELA',self.client.get('/').text)
        self.assertEqual(self.client.get('/assets/styles.css').status_code,200)
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/dados/sentinela.db').status_code,404)
        self.assertEqual(self.client.get('/api/overview').json()['summary']['cameras'],0)

    def test_accepted_event_populates_camera_and_history(self):
        event=self.event()
        self.assertEqual(self.client.post('/api/events',json=event).status_code,201)
        overview=self.client.get('/api/overview').json()
        self.assertEqual(overview['summary']['presence'],1)
        self.assertEqual(overview['cameras'][0]['count'],1)
        history=self.client.get('/api/events').json()
        self.assertEqual(history['total'],1)
        self.assertEqual(self.client.get('/api/events/'+event['event_id']).json()['config'],{'imgsz':320})

    def test_retry_is_idempotent(self):
        event=self.event()
        self.assertEqual(self.client.post('/api/events',json=event).status_code,201)
        response=self.client.post('/api/events',json=event)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['inserted'])
        self.assertEqual(self.client.get('/api/events').json()['total'],1)

    def test_reused_id_with_different_payload_is_rejected(self):
        event=self.event()
        self.client.post('/api/events',json=event)
        event['count']=7
        self.assertEqual(self.client.post('/api/events',json=event).status_code,409)
        self.assertEqual(self.client.get('/api/overview').json()['cameras'][0]['count'],1)

    def test_conflicting_sequence_does_not_insert_another_event(self):
        self.client.post('/api/events',json=self.event())
        self.assertEqual(self.client.post('/api/events',json=self.event()).status_code,409)

    def test_camera_cannot_reuse_another_camera_session(self):
        self.client.post('/api/events',json=self.event())
        response=self.client.post('/api/events',json=self.event(2,camera_id='webcam-02'))
        self.assertEqual(response.status_code,409)

    def test_delayed_event_does_not_overwrite_newer_state(self):
        self.client.post('/api/events',json=self.event(2))
        older=self.event(1,observed_at=(self.now-timedelta(seconds=10)).isoformat(),count=0,
                         state='SEM_PESSOAS_DETECTADAS')
        self.assertEqual(self.client.post('/api/events',json=older).status_code,201)
        self.assertEqual(self.client.get('/api/overview').json()['cameras'][0]['count'],1)

    def test_old_backlog_received_now_is_not_live(self):
        old=self.event(observed_at=(self.now-timedelta(minutes=5)).isoformat())
        self.client.post('/api/events',json=old)
        camera=self.client.get('/api/overview').json()['cameras'][0]
        self.assertEqual(camera['effective_state'],'SEM_COMUNICACAO')
        self.assertIsNone(camera['count'])

    def test_missing_heartbeat_is_unknown_instead_of_empty(self):
        self.client.post('/api/events',json=self.event())
        self.now+=timedelta(seconds=16)
        overview=self.client.get('/api/overview').json()
        self.assertEqual(overview['summary']['presence'],0)
        self.assertEqual(overview['summary']['unavailable'],1)
        self.assertIsNone(overview['cameras'][0]['count'])

    def test_invalid_count_and_missing_timezone_are_rejected(self):
        for changes in ({'state':'MONITORAMENTO_INDISPONIVEL','count':0},
                        {'count':-1},{'score':1.5},{'observed_at':self.now.replace(tzinfo=None).isoformat()}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/api/events',json=self.event(**changes)).status_code,422)

    def test_future_clock_does_not_poison_latest_camera_state(self):
        future=self.event(observed_at=(self.now+timedelta(minutes=5)).isoformat())
        self.assertEqual(self.client.post('/api/events',json=future).status_code,409)

    def test_filters_pagination_and_heartbeat_visibility(self):
        for sequence in range(1,4):
            self.client.post('/api/events',json=self.event(sequence,
                reason='heartbeat' if sequence==3 else 'mudanca_estado'))
        self.assertEqual(self.client.get('/api/events').json()['total'],2)
        page=self.client.get('/api/events?only_changes=false&limit=1&offset=1').json()
        self.assertEqual(page['total'],3)
        self.assertEqual(len(page['items']),1)
        self.assertEqual(self.client.get('/api/events?camera_id=outra').json()['total'],0)
        self.assertEqual(self.client.get('/api/events?state=SEM_PESSOAS_DETECTADAS').json()['total'],0)
        self.assertEqual(self.client.get('/api/events?limit=999').status_code,422)

    def test_csv_preserves_unknown_and_escapes_formulas(self):
        self.client.post('/api/events',json=self.event(state='MONITORAMENTO_INDISPONIVEL',count=None,
                                                      sala='=1+2',reason='erro'))
        response=self.client.get('/api/events.csv')
        self.assertIn("'=1+2",response.text)
        self.assertIn('MONITORAMENTO_INDISPONIVEL;;',response.text)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))

    def test_events_survive_reopening_database(self):
        self.client.post('/api/events',json=self.event())
        with TestClient(create_app(self.database,clock=lambda:self.now)) as restarted:
            self.assertEqual(restarted.get('/api/events').json()['total'],1)

    def test_all_history_can_show_events_older_than_a_month(self):
        self.client.post('/api/events',json=self.event(observed_at=(self.now-timedelta(days=50)).isoformat()))
        self.assertEqual(self.client.get('/api/events').json()['total'],0)
        self.assertEqual(self.client.get('/api/events?hours=0').json()['total'],1)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)
        self.client=TestClient(create_app(self.path/'api.db'))
        self.client.__enter__()
        self.addCleanup(lambda:self.client.__exit__(None,None,None))
        self.publisher=Publisher('http://127.0.0.1:8000',self.path/'fila.db','webcam-01','Sala de testes',
                                 {'modelo':'yolo26n.pt','imgsz':320},autostart=False)

    def record(self,reason='mudanca_estado'):
        self.publisher.record('PRESENCA_DETECTADA',1,0.9,20,0.65,reason,50,60)

    def bridge(self,request,timeout=2):
        response=self.client.post('/api/events',json=json.loads(request.data))
        if response.status_code >= 400:
            raise HTTPError(request.full_url,response.status_code,'rejected',{},io.BytesIO(response.content))
        class Result:
            status=response.status_code
            def __enter__(self): return self
            def __exit__(self,*_): pass
        return Result()

    def test_network_failure_keeps_persistent_event_for_restart(self):
        self.record()
        original=self.publisher.outbox.first()['payload']
        with patch('envio_api.urlopen',side_effect=URLError('offline')):
            with self.assertRaises(URLError): self.publisher.deliver_one()
        reopened=Outbox(self.path/'fila.db')
        self.assertEqual(reopened.pending(),1)
        self.assertEqual(reopened.first()['payload'],original)

    def test_api_commit_then_network_timeout_retries_without_duplication(self):
        self.record()
        def accepted_then_timeout(request,timeout=2):
            self.bridge(request,timeout)
            raise URLError('resposta perdida')
        with patch('envio_api.urlopen',side_effect=accepted_then_timeout):
            with self.assertRaises(URLError): self.publisher.deliver_one()
        self.assertEqual(self.publisher.outbox.pending(),1)
        with patch('envio_api.urlopen',side_effect=self.bridge):
            self.assertTrue(self.publisher.deliver_one())
        self.assertEqual(self.publisher.outbox.pending(),0)
        self.assertEqual(self.client.get('/api/events').json()['total'],1)

    def test_permanent_rejection_does_not_block_later_events(self):
        self.record()
        with self.publisher.outbox.connection() as connection:
            row=connection.execute('SELECT id,payload FROM pending').fetchone()
            invalid=json.loads(row['payload']); invalid['count']=-1
            connection.execute('UPDATE pending SET payload=? WHERE id=?',(json.dumps(invalid),row['id']))
        self.record()
        with patch('envio_api.urlopen',side_effect=self.bridge),redirect_stdout(io.StringIO()):
            self.publisher.deliver_one(); self.publisher.deliver_one()
        self.assertEqual(self.publisher.outbox.pending(),0)
        with self.publisher.outbox.connection() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM rejected').fetchone()[0],1)
        self.assertEqual(self.client.get('/api/events').json()['total'],1)

    def test_stop_event_uses_unknown_count(self):
        self.record(reason='encerramento_voluntario')
        event=json.loads(self.publisher.outbox.first()['payload'])
        self.assertEqual(event['state'],'MONITORAMENTO_INDISPONIVEL')
        self.assertIsNone(event['count'])
        with patch('envio_api.urlopen',side_effect=self.bridge): self.publisher.deliver_one()
        self.assertEqual(self.client.get('/api/overview').json()['summary']['unavailable'],1)

    def test_old_destination_does_not_block_events_for_new_api_address(self):
        self.record()
        new=Publisher('http://127.0.0.1:8001',self.path/'fila.db','webcam-01','Sala de testes',
                       {'modelo':'yolo26n.pt','imgsz':320},autostart=False)
        new.record('PRESENCA_DETECTADA',1,0.9,20,0.65,'mudanca_estado',50,60)
        with patch('envio_api.urlopen',side_effect=self.bridge):
            self.assertTrue(new.deliver_one())
            self.assertFalse(new.deliver_one())
        self.assertEqual(self.publisher.outbox.pending(),1)
        self.assertEqual(self.publisher.outbox.first()['endpoint'],'http://127.0.0.1:8000/api/events')

    def test_periodic_samples_are_throttled_but_state_changes_are_not(self):
        with patch('envio_api.time.monotonic',return_value=10):
            self.record('amostra')
        with patch('envio_api.time.monotonic',return_value=11):
            self.record('amostra')
            self.record('mudanca_estado')
        self.assertEqual(self.publisher.outbox.pending(),2)

    def test_webcam_logger_publishes_a_valid_event(self):
        from webcam_yolo import SessionLog, parse_args
        args=parse_args(['--saida',str(self.path/'logs'),'--fila',str(self.path/'fila.db')])
        with patch('envio_api.Publisher',return_value=self.publisher):
            logger=SessionLog(args)
            logger.record('PRESENCA_DETECTADA',1,0.8,20,0.65,'mudanca_estado',50,60)
            logger.close()
        with patch('envio_api.urlopen',side_effect=self.bridge): self.publisher.deliver_one()
        self.assertEqual(self.client.get('/api/overview').json()['summary']['presence'],1)


if __name__ == '__main__':
    unittest.main()
