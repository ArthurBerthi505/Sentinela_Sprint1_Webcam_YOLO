"""Envia eventos fictícios ao painel sem webcam: python simular.py."""
import argparse
from pathlib import Path
import time

from envio_api import Publisher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--duracao", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.duracao <= 3600:
        parser.error("--duracao deve estar entre 1 e 3600 segundos.")
    root = Path(__file__).resolve().parent
    clients = [Publisher(args.api_url, root/"dados"/f"fila_demo_{index}.sqlite3", f"demo-0{index}", sala,
                         {"modelo": "simulador", "imgsz": 320, "descricao": "Dados fictícios para testar a integração"},
                         mode="simulacao")
               for index, sala in enumerate(("Recepção", "Sala técnica"), start=1)]
    previous = [None, None]
    print(f"Enviando SIMULAÇÃO por {args.duracao}s. Painel: {args.api_url}")
    try:
        for tick in range(args.duracao):
            counts = [1 if tick % 20 < 12 else 0, 2 if tick % 24 >= 12 else 0]
            for index, (client, count) in enumerate(zip(clients, counts)):
                state = "PRESENCA_DETECTADA" if count else "SEM_PESSOAS_DETECTADAS"
                reason = "mudanca_estado" if state != previous[index] else "amostra"
                client.record(state, count, 0.93 if count else 0, 20, 0.65, reason, 50, 65)
                previous[index] = state
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for client in clients:
            client.record("MONITORAMENTO_INDISPONIVEL", None, 0, 0, 0.65, "encerramento_voluntario")
            client.close()
    print("Simulação encerrada. O histórico permanece no banco.")


if __name__ == "__main__":
    main()
