import os
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
import httpx

from database import init_db, get_conn, get_cfg, set_cfg, log_evento
from geo import montar_fila_cidades
from bloqueios import validar_3_bloqueios
from onedrive_excel import gerar_excel_mes, sincronizar_onedrive
from investigador import loop_autonomo_24_7

OWNER_EMAIL = os.getenv("OWNER_EMAIL", "vorticesolucoesglobais@gmail.com").strip().lower()
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "common")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "vortice-chave-secreta-2026")

app = FastAPI(title="Votice Investigador Privado")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False)


@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(loop_autonomo_24_7())


def exigir_dono(request: Request):
    user_email = request.session.get("user_email", "").lower()
    if not user_email or user_email != OWNER_EMAIL:
        raise HTTPException(status_code=403, detail="Acesso exclusivo do proprietário.")
    return user_email


@app.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/callback"
    scope = "User.Read Files.ReadWrite Calendars.ReadWrite offline_access"
    auth_url = (
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/authorize"
        f"?client_id={AZURE_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&response_mode=query"
        f"&scope={scope}"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = ""):
    if not code:
        return HTMLResponse("<h2>Erro de autenticação Microsoft.</h2>", status_code=400)

    redirect_uri = str(request.base_url).rstrip("/") + "/auth/callback"
    token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(token_url, data=data)
        tokens = r.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return HTMLResponse("<h2>Falha ao obter token Microsoft.</h2>", status_code=401)

        me = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        perfil = me.json()
        email = (perfil.get("mail") or perfil.get("userPrincipalName") or "").strip().lower()

        if email != OWNER_EMAIL:
            log_evento(email or "desconhecido", "Tentativa bloqueada", "Conta não autorizada")
            return HTMLResponse("<h2>Acesso negado. Este sistema pertence exclusivamente a Mathews.</h2>", status_code=403)

        if tokens.get("refresh_token"):
            set_cfg("ms_refresh_token", tokens["refresh_token"])

        request.session["user_email"] = email
        log_evento(email, "Login Microsoft Autorizado", "Acesso ao painel")
        return RedirectResponse("/")


@app.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_email = request.session.get("user_email", "").lower()
    if user_email != OWNER_EMAIL:
        return HTMLResponse("""
        <!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Votice Investigador</title>
        <style>body{background:#090b0f;color:#e8edf4;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
        .box{background:#131920;border:1px solid #22385a;padding:32px;border-radius:12px;max-width:420px;text-align:center}
        a{display:inline-block;margin-top:16px;padding:12px 24px;background:#7fb0e6;color:#000;font-weight:700;text-decoration:none;border-radius:8px}</style>
        </head><body><div class="box"><h2>VOTICE INVESTIGADOR</h2><p>Acesso privado exclusivo via conta Microsoft.</p>
        <a href="/auth/login">Entrar com Microsoft</a></div></body></html>
        """)
    return FileResponse("web/index.html")


@app.get("/api/estado")
async def api_estado(request: Request):
    exigir_dono(request)
    mes_ref = datetime.now().strftime("%m-%Y")
    conn = get_conn()
    empresas = [dict(r) for r in conn.execute("SELECT * FROM empresas WHERE mes_ref = ? ORDER BY id DESC", (mes_ref,)).fetchall()]
    modulos = [dict(r) for r in conn.execute("SELECT * FROM modulos").fetchall()]
    sites = [dict(r) for r in conn.execute("SELECT * FROM sites").fetchall()]
    portes = [dict(r) for r in conn.execute("SELECT * FROM portes").fetchall()]
    logs = [dict(r) for r in conn.execute("SELECT * FROM logs_auditoria ORDER BY id DESC LIMIT 15").fetchall()]
    fila_total = conn.execute("SELECT COUNT(*) as c FROM fila_cidades").fetchone()["c"]
    fila_feitas = conn.execute("SELECT COUNT(*) as c FROM fila_cidades WHERE status = 'concluido'").fetchone()["c"]
    cidade_atual = conn.execute("SELECT * FROM fila_cidades WHERE status IN ('investigando','pendente') ORDER BY id ASC LIMIT 1").fetchone()
    conn.close()

    return {
        "mes_ref": mes_ref,
        "worker_status": get_cfg("worker_status"),
        "regiao_ativa": get_cfg("regiao_ativa"),
        "fila_total": fila_total,
        "fila_feitas": fila_feitas,
        "cidade_atual": dict(cidade_atual) if cidade_atual else None,
        "empresas": empresas,
        "modulos": modulos,
        "sites": sites,
        "portes": portes,
        "logs": logs
    }


@app.post("/api/varredura/iniciar")
async def api_iniciar_varredura(request: Request):
    email = exigir_dono(request)
    body = await request.json()
    cidades = await montar_fila_cidades(body)

    if not cidades:
        return {"ok": False, "erro": "Nenhuma cidade encontrada com esses filtros. Ajuste os filtros."}

    qtd_por_cidade = int(body.get("empresas_por_cidade", 20))
    conn = get_conn()
    conn.execute("DELETE FROM fila_cidades")
    for c in cidades:
        conn.execute(
            "INSERT INTO fila_cidades (cidade, uf, populacao, empresas_meta) VALUES (?, ?, ?, ?)",
            (c["nome"], c["uf"], c.get("populacao", 0), qtd_por_cidade)
        )
    conn.commit()
    conn.close()

    set_cfg("regiao_ativa", body.get("modo", "Personalizada"))
    set_cfg("notif_modo", body.get("notif_modo", "email"))
    set_cfg("worker_status", "rodando")
    log_evento(email, "Varredura Iniciada", f"{len(cidades)} cidades na fila")
    return {"ok": True, "total_cidades": len(cidades), "primeira": cidades[0]["nome"]}


@app.post("/api/varredura/comando/{cmd}")
async def api_comando(cmd: str, request: Request):
    email = exigir_dono(request)
    cmd = cmd.upper()
    if cmd == "P":
        set_cfg("worker_status", "pausado")
        await sincronizar_onedrive()
        log_evento(email, "Pausado (P)", "Varredura pausada e salva no OneDrive")
        return {"ok": True, "msg": "Pausado e salvo no OneDrive."}
    elif cmd == "R":
        set_cfg("worker_status", "rodando")
        log_evento(email, "Retomado (R)", "Varredura retomada")
        return {"ok": True, "msg": "Retomado de onde parou."}
    elif cmd == "S":
        set_cfg("worker_status", "parado")
        await sincronizar_onedrive()
        log_evento(email, "Salvo e Encerrado (S)", "Planilha sincronizada")
        return {"ok": True, "msg": "Salvo e encerrado."}
    elif cmd == "E":
        await sincronizar_onedrive()
        log_evento(email, "Exportado na Hora (E)", "Excel sincronizado no OneDrive")
        return {"ok": True, "msg": "Excel atualizado no OneDrive!"}
    raise HTTPException(status_code=400, detail="Comando inválido")


@app.post("/api/empresa/manual")
async def api_empresa_manual(request: Request):
    email = exigir_dono(request)
    body = await request.json()
    aprovado, erros, score = validar_3_bloqueios(body)
    if not aprovado:
        log_evento(email, "Descartada por falta de prova", body.get("nome", ""))
        return {"ok": False, "erros": erros}

    mes_ref = datetime.now().strftime("%m-%Y")
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO empresas
           (mes_ref, data_investigacao, cidade, populacao, nome, telefone, dono, instagram,
            porte, score, modulos, sinais, provas_json, sites_verificados, conclusao,
            status, quem_investigou, filtro_usado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aguardando Revisão', ?, 'Busca Manual')""",
        (
            mes_ref,
            datetime.now().strftime("%d/%m/%Y"),
            body.get("cidade", ""),
            body.get("populacao", "NÃO ENCONTRADO"),
            body["nome"],
            body["telefone"],
            body["dono"],
            body["instagram"],
            body["porte"],
            score,
            ", ".join(m["nome"] for m in body.get("modulos", [])),
            body.get("sinais", ""),
            " | ".join(p["url"] for p in body.get("provas", [])),
            " + ".join({p["site"] for p in body.get("provas", [])}),
            "Aguardando revisão manual",
            email
        )
    )
    conn.commit()
    conn.close()
    await sincronizar_onedrive(mes_ref)
    return {"ok": True, "score": score}


@app.post("/api/empresa/{emp_id}/status")
async def api_mudar_status(emp_id: int, request: Request):
    email = exigir_dono(request)
    body = await request.json()
    novo = body.get("status", "Aprovado para Contato")
    conn = get_conn()
    conn.execute("UPDATE empresas SET status = ? WHERE id = ?", (novo, emp_id))
    conn.commit()
    conn.close()
    log_evento(email, "Status Alterado", f"Empresa #{emp_id} -> {novo}")
    await sincronizar_onedrive()
    return {"ok": True}


@app.post("/api/cadastro/{tipo}")
async def api_cadastrar_item(tipo: str, request: Request):
    email = exigir_dono(request)
    body = await request.json()
    conn = get_conn()
    if tipo == "modulo":
        conn.execute("INSERT OR IGNORE INTO modulos (nome, sinais, ativo) VALUES (?, ?, 1)", (body["nome"], body.get("sinais", "")))
    elif tipo == "site":
        conn.execute("INSERT OR IGNORE INTO sites (nome, url_base, tipo, prova_desc, ativo) VALUES (?, ?, ?, ?, 1)", (body["nome"], body.get("url_base", ""), body.get("tipo", "Outro"), body.get("prova_desc", "")))
    elif tipo == "porte":
        conn.execute("INSERT OR IGNORE INTO portes (nome, faixa, descricao, ativo) VALUES (?, ?, ?, 1)", (body["nome"], body.get("faixa", ""), body.get("descricao", "")))
    conn.commit()
    conn.close()
    log_evento(email, f"Novo {tipo} adicionado", body.get("nome", ""))
    return {"ok": True}


@app.post("/api/config/notificacoes")
async def api_salvar_notif(request: Request):
    email = exigir_dono(request)
    body = await request.json()
    for k, v in body.items():
        set_cfg(k, v)
    log_evento(email, "Notificações Atualizadas", body.get("notif_modo", ""))
    return {"ok": True}


@app.get("/api/excel/baixar")
async def api_baixar_excel(request: Request):
    exigir_dono(request)
    mes_ref = datetime.now().strftime("%m-%Y")
    caminho = gerar_excel_mes(mes_ref)
    return FileResponse(str(caminho), filename=f"VOTICE_INVESTIGADOR_{mes_ref}.xlsx")
