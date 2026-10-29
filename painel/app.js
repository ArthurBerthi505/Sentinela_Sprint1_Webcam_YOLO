'use strict';
const $ = id => document.getElementById(id);
const stateLabels = {VERIFICANDO:'Verificando',PRESENCA_DETECTADA:'Presença detectada',SEM_PESSOAS_DETECTADAS:'Sem pessoas detectadas',MONITORAMENTO_INDISPONIVEL:'Indisponível',SEM_COMUNICACAO:'Sem comunicação'};
const stateClasses = {VERIFICANDO:'verifying',PRESENCA_DETECTADA:'presence',SEM_PESSOAS_DETECTADAS:'empty',MONITORAMENTO_INDISPONIVEL:'unavailable',SEM_COMUNICACAO:'unavailable'};
const reasonLabels = {inicio:'Início da sessão',mudanca_estado:'Mudança de estado',heartbeat:'Atualização periódica',ajuste_confianca:'Ajuste de confiança',erro:'Falha de monitoramento',quadro_atrasado:'Imagem atrasada',encerramento_voluntario:'Sessão encerrada',interrupcao_usuario:'Sessão interrompida'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const number = value => new Intl.NumberFormat('pt-BR').format(value);
const milliseconds = value => value == null ? '—' : `${number(Math.round(value))} ms`;
const clock = value => new Date(value).toLocaleTimeString('pt-BR');
const date = value => new Date(value).toLocaleDateString('pt-BR');
const badge = state => `<span class="badge ${stateClasses[state] || 'verifying'}">${esc(stateLabels[state] || state)}</span>`;
let offset = 0, total = 0, lastOverview = null, loading = false, historyLoading = false;
const pageSize = 20;

async function request(path) {
  const response = await fetch(path, {signal: AbortSignal.timeout(6000),cache:'no-store'});
  if (!response.ok) throw new Error(`A API respondeu HTTP ${response.status}.`);
  return response.json();
}
function connection(online) {
  $('connection').className = `connection ${online ? '' : 'offline'}`;
  $('connection').querySelector('span').textContent = online ? 'API conectada' : 'API indisponível';
  $('error-banner').hidden = online;
  if (!online) $('error-banner').textContent = 'Não foi possível atualizar os dados. As leituras atuais estão indisponíveis. Verifique se o terminal do servidor continua aberto.';
}
function renderCameras(cameras) {
  $('camera-total').textContent = cameras.length;
  if (!cameras.length) {
    $('cameras').innerHTML = '<div class="empty-state"><div class="empty-icon" aria-hidden="true">◫</div><h3>Pronto para receber a primeira câmera</h3><p>Inicie o detector em um segundo terminal, na pasta do projeto. O ambiente aparecerá aqui quando chegar o primeiro evento.</p><code>.\\.venv\\Scripts\\python.exe webcam_yolo.py</code><p style="margin-top:15px">Para testar sem câmera, execute <strong>simular.py</strong>. Os ambientes serão identificados como simulação.</p></div>';
    return;
  }
  $('cameras').innerHTML = cameras.map(camera => {
    const state = camera.effective_state;
    const unknown = ['SEM_COMUNICACAO','MONITORAMENTO_INDISPONIVEL'].includes(state);
    const simulation = camera.mode === 'simulacao';
    const count = camera.count == null ? '—' : String(camera.count).padStart(2,'0');
    const age = camera.seconds_since_observation;
    const seen = age < 60 ? `há ${Math.floor(age)} s` : age < 3600 ? `há ${Math.floor(age/60)} min` : date(camera.observed_at);
    return `<article class="camera-card ${state === 'PRESENCA_DETECTADA' ? 'online-presence' : unknown ? 'offline' : ''}">
      <div class="camera-top"><span class="camera-id">${esc(camera.camera_id)}</span>${badge(state)}</div>
      <h3>${esc(camera.sala)}</h3><span class="source-label ${simulation ? 'simulation' : ''}">${simulation ? 'SIMULAÇÃO · Dados fictícios' : 'WEBCAM · Análise local'}</span>
      <div class="camera-reading"><div class="reading-number">${count}<small>${unknown ? 'Leitura atual indisponível' : 'Pessoa(s) detectada(s) no quadro'}</small></div><div class="timing">${unknown ? '—' : milliseconds(camera.inference_ms)}<small>Tempo de inferência</small></div></div>
      <div class="camera-bottom"><span>Última observação ${esc(seen)}</span><span>Conf. ${number(camera.confidence)}</span></div>
    </article>`;
  }).join('');
}
function updateCameraFilter(cameras) {
  const selected = $('filter-camera').value;
  const fragment = document.createDocumentFragment();
  fragment.append(new Option('Todos os ambientes',''));
  for (const camera of cameras) fragment.append(new Option(`${camera.sala} · ${camera.camera_id}`,camera.camera_id));
  $('filter-camera').replaceChildren(fragment);
  if (cameras.some(camera => camera.camera_id === selected)) $('filter-camera').value = selected;
}
function renderOverview(data) {
  lastOverview = data;
  const stats = data.summary;
  for (const [id,key] of [['metric-cameras','cameras'],['metric-presence','presence'],['metric-unavailable','unavailable'],['metric-events','events_24h']]) $(id).textContent = number(stats[key]);
  renderCameras(data.cameras);
  updateCameraFilter(data.cameras);
  $('updated').textContent = `Atualizado às ${clock(data.server_time)}`;
}
function query() {
  const params = new URLSearchParams({hours:$('filter-hours').value,only_changes:String(!$('filter-heartbeats').checked)});
  if ($('filter-camera').value) params.set('camera_id',$('filter-camera').value);
  if ($('filter-state').value) params.set('state',$('filter-state').value);
  return params;
}
async function loadHistory() {
  if (historyLoading) return;
  historyLoading = true;
  try {
    const params = query(); params.set('limit',pageSize); params.set('offset',offset);
    const data = await request(`/api/events?${params}`);
    total = data.total;
    $('events').innerHTML = data.items.length ? data.items.map(event => `<tr>
      <td>${clock(event.observed_at)}<small>${date(event.observed_at)}</small></td>
      <td>${esc(event.sala)}<small>${esc(event.camera_id)}</small></td>
      <td>${badge(event.state)}<small>${esc(reasonLabels[event.reason] || event.reason)}</small></td>
      <td>${event.count == null ? '—' : number(event.count)}</td>
      <td>${milliseconds(event.inference_ms)}</td>
      <td>${event.mode === 'simulacao' ? '<span class="amber">Simulação</span>' : 'Webcam'}</td>
      <td><button type="button" class="detail-button" data-event="${esc(event.event_id)}">Detalhes</button></td></tr>`).join('') : '<tr><td colspan="7" class="table-empty">Nenhum evento encontrado para os filtros selecionados.</td></tr>';
    $('event-total').textContent = total ? `${number(offset+1)}–${number(Math.min(offset+pageSize,total))} de ${number(total)} eventos` : '0 eventos';
    $('page').textContent = Math.floor(offset/pageSize)+1;
    $('previous').disabled = offset === 0;
    $('next').disabled = offset+pageSize >= total;
  } catch (error) {
    $('event-total').textContent = 'Falha ao atualizar o histórico. Tente novamente.';
  } finally { historyLoading = false; }
}
async function update(forceHistory=false) {
  if (loading) return;
  loading = true;
  try {
    const data = await request('/api/overview');
    connection(true); renderOverview(data);
    if (offset === 0 || forceHistory) await loadHistory();
  } catch (error) {
    connection(false);
    for (const id of ['metric-cameras','metric-presence','metric-unavailable','metric-events']) $(id).textContent = '—';
    if (lastOverview) renderCameras(lastOverview.cameras.map(camera => ({...camera,effective_state:'SEM_COMUNICACAO',count:null})));
    else $('cameras').innerHTML = '<div class="empty-state"><h3>Aguardando conexão com a API</h3><p>Inicie o servidor com <strong>python servidor.py</strong> e mantenha o terminal aberto.</p></div>';
    $('updated').textContent = 'Atualização interrompida';
  } finally { loading = false; }
}
$('filters').addEventListener('submit',event=>event.preventDefault());
$('filters').addEventListener('change',()=>{offset=0;loadHistory();});
$('refresh').addEventListener('click',()=>{offset=0;update(true);});
$('previous').addEventListener('click',()=>{offset=Math.max(0,offset-pageSize);loadHistory();});
$('next').addEventListener('click',()=>{if(offset+pageSize<total){offset+=pageSize;loadHistory();}});
$('export').addEventListener('click',()=>{window.location.href=`/api/events.csv?${query()}`;});
$('events').addEventListener('click',async event=>{
  const button = event.target.closest('[data-event]');
  if (!button) return;
  $('event-json').textContent = 'Carregando registro…'; $('details').showModal();
  try { $('event-json').textContent = JSON.stringify(await request(`/api/events/${encodeURIComponent(button.dataset.event)}`),null,2); }
  catch (error) { $('event-json').textContent = 'Não foi possível carregar o evento. Verifique a conexão e tente novamente.'; }
});
$('close-details').addEventListener('click',()=>$('details').close());
document.querySelectorAll('.nav-link').forEach(link=>link.addEventListener('click',()=>{
  document.querySelectorAll('.nav-link').forEach(other=>other.classList.toggle('active',other===link));
}));
update();
setInterval(()=>{if(!document.hidden)update();},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)update();});
