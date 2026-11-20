import asyncio
import random
import json
from datetime import datetime
import httpx
from database import get_conn, get_cfg, set_cfg, log_evento
from geo import buscar_empresas_azure_maps
from bloqueios import validar_3_bloqueios
from onedrive_excel import sincronizar_onedrive, gerar_excel_mes


async def enviar_notificacao(titulo: str, mensagem: str):
    modo = get_cfg("notif_modo", "email")
    if modo == "nenhum":
        return

    webhook = get_cfg("notif_webhook", "").strip()
    zap = get_cfg("notif_whatsapp", "").strip()
    zap_token = get_cfg("notif_zap_token", "").strip()

    async with httpx.AsyncClient(timeout=10.0) as client:
        if modo in ("whatsapp", "ambos") and zap and zap_token:
            try:
                await client.post(
                    zap_token,
                    json={"phone": zap, "message": f"*{titulo}*\n{mensagem}"}
                )
            except Exception:
                pass

        if webhook:
            try:
                await client.post(webhook, json={"title": titulo, "text": mensagem})
            except Exception:
                pass


async def verificar_url_real(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for _ in range(2):
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code < 400:
                    return True
            except Exception:
                await asyncio.sleep(1)
    return False


async def investigar_empresa_nos_sites(empresa: dict, cidade: str, uf: str, sites_ativos: list, modulos_ativos: list):
    """
    Verifica fontes públicas reais sem inventar nenhum dado.
    random é usado APENAS para intervalo de espera (1.2s a 2.5s).
    """
    provas = []
    sites_checados = []

    if empresa.get("site_oficial") and await verificar_url_real(empresa["site_oficial"]):
        provas.append({
            "site": "Site Oficial / Azure Maps",
            "url": empresa["site_oficial"],
            "evidencia": f"Cadastro localizado via Azure Maps ({empresa.get('categoria', 'Comércio')})"
        })
        sites_checados.append("Azure Maps")

    # Respeita intervalo entre requisições (Bloqueio 2: random apenas para delay)
    await asyncio.sleep(random.uniform(1.2, 2.5))

    return provas, sites_checados


async def loop_autonomo_24_7():
    ultimo_mes = datetime.now().strftime("%m-%Y")

    while True:
        try:
            mes_atual = datetime.now().strftime("%m-%Y")
            if mes_atual != ultimo_mes:
                gerar_excel_mes(ultimo_mes)
                await sincronizar_onedrive(ultimo_mes)
                gerar_excel_mes(mes_atual)
                await sincronizar_onedrive(mes_atual)
                log_evento("SISTEMA", "Virada de Mês Automática", f"Fechado {ultimo_mes} e aberto {mes_atual}")
                ultimo_mes = mes_atual

            status_worker = get_cfg("worker_status", "parado")
            if status_worker != "rodando":
                await asyncio.sleep(3)
                continue

            conn = get_conn()
            prox = conn.execute(
                "SELECT * FROM fila_cidades WHERE status = 'pendente' ORDER BY id ASC LIMIT 1"
            ).fetchone()

            if not prox:
                conn.close()
                set_cfg("worker_status", "concluido")
                log_evento("SISTEMA", "Varredura Concluída", "Todas as cidades da fila foram processadas.")
                await sincronizar_onedrive(mes_atual)
                await asyncio.sleep(5)
                continue

            cidade_id = prox["id"]
            cidade_nome = prox["cidade"]
            uf = prox["uf"]
            pop = prox["populacao"]
            meta = prox["empresas_meta"]

            conn.execute("UPDATE fila_cidades SET status = 'investigando' WHERE id = ?", (cidade_id,))
            sites_ativos = [dict(r) for r in conn.execute("SELECT * FROM sites WHERE ativo = 1").fetchall()]
            modulos_ativos = [dict(r) for r in conn.execute("SELECT * FROM modulos WHERE ativo = 1").fetchall()]
            conn.commit()
            conn.close()

            candidatas = await buscar_empresas_azure_maps(cidade_nome, uf, limite=meta)

            for emp in candidatas:
                if get_cfg("worker_status") != "rodando":
                    break

                provas, sites_checados = await investigar_empresa_nos_sites(
                    emp, cidade_nome, uf, sites_ativos, modulos_ativos
                )

                dossie = {
                    "nome": emp["nome"],
                    "telefone": emp["telefone"],
                    "dono": "NÃO ENCONTRADO",
                    "instagram": "NÃO ENCONTRADO",
                    "porte": "A VERIFICAR",
                    "populacao": str(pop) if pop else "NÃO ENCONTRADO",
                    "provas": provas,
                    "modulos": []
                }

                aprovado, erros, score = validar_3_bloqueios(dossie)
                if not aprovado:
                    log_evento("ROBÔ", "Descartada por falta de prova", f"{emp['nome']} ({cidade_nome}-{uf})")
                    continue

                conn2 = get_conn()
                try:
                    conn2.execute(
                        """INSERT OR IGNORE INTO empresas
                           (mes_ref, data_investigacao, cidade, populacao, nome, telefone, dono, instagram,
                            porte, score, modulos, sinais, provas_json, sites_verificados, conclusao,
                            status, quem_investigou, filtro_usado)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aguardando Revisão', 'Robô 24/7', ?)""",
                        (
                            mes_atual,
                            datetime.now().strftime("%d/%m/%Y"),
                            f"{cidade_nome} - {uf}",
                            dossie["populacao"],
                            dossie["nome"],
                            dossie["telefone"],
                            dossie["dono"],
                            dossie["instagram"],
                            dossie["porte"],
                            score,
                            ", ".join(m["nome"] for m in dossie["modulos"]),
                            "Provas reais verificadas",
                            " | ".join(p["url"] for p in provas),
                            " + ".join(sites_checados),
                            "Aguardando revisão manual",
                            get_cfg("regiao_ativa")
                        )
                    )
                    conn2.commit()
                    await enviar_notificacao(
                        "Nova Empresa Aguardando Revisão",
                        f"{dossie['nome']} - {cidade_nome}/{uf} (Score {score})"
                    )
                finally:
                    conn2.close()

            conn3 = get_conn()
            conn3.execute(
                "UPDATE fila_cidades SET status = 'concluido', empresas_feitas = ? WHERE id = ?",
                (len(candidatas), cidade_id)
            )
            conn3.commit()
            conn3.close()

            await sincronizar_onedrive(mes_atual)

        except Exception as e:
            log_evento("SISTEMA", "Erro recuperado automaticamente", str(e))
            await asyncio.sleep(5)
