import os
from datetime import datetime
from pathlib import Path
import httpx
import openpyxl
from openpyxl.styles import Font, PatternFill
from database import get_conn, get_cfg, set_cfg, log_evento

EXPORT_DIR = Path("/app/exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

MESES_PT = {
    1: "01-Janeiro", 2: "02-Fevereiro", 3: "03-Março", 4: "04-Abril",
    5: "05-Maio", 6: "06-Junho", 7: "07-Julho", 8: "08-Agosto",
    9: "09-Setembro", 10: "10-Outubro", 11: "11-Novembro", 12: "12-Dezembro"
}

COLUNAS_EXCEL = [
    "Data da Investigação",
    "Cidade",
    "População da Cidade",
    "Nome da Empresa",
    "Telefone (Fonte Real)",
    "Nome do Dono (Fonte Real)",
    "Instagram",
    "Score Déficit",
    "Módulos Identificados",
    "Sinais Encontrados",
    "Provas com Link Real",
    "Sites Verificados",
    "Conclusão",
    "Status",
    "Quem Investigou"
]


def gerar_excel_mes(mes_ref: str = None) -> Path:
    agora = datetime.now()
    if not mes_ref:
        mes_ref = agora.strftime("%m-%Y")

    arquivo = EXPORT_DIR / f"VOTICE_INVESTIGADOR_{mes_ref}.xlsx"
    wb = openpyxl.Workbook()

    # Aba 1: Dossiês do Mês
    ws1 = wb.active
    ws1.title = "Dossiês do Mês"
    ws1.append(COLUNAS_EXCEL)

    header_fill = PatternFill(start_color="1B222C", end_color="1B222C", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws1[1]:
        cell.fill = header_fill
        cell.font = header_font

    conn = get_conn()
    empresas = conn.execute(
        "SELECT * FROM empresas WHERE mes_ref = ? ORDER BY id ASC", (mes_ref,)
    ).fetchall()

    for e in empresas:
        ws1.append([
            e["data_investigacao"],
            e["cidade"],
            e["populacao"],
            e["nome"],
            e["telefone"],
            e["dono"],
            e["instagram"],
            e["score"],
            e["modulos"],
            e["sinais"],
            e["provas_json"],
            e["sites_verificados"],
            e["conclusao"],
            e["status"],
            e["quem_investigou"]
        ])

    # Aba 2: Resumo do Mês
    ws2 = wb.create_sheet(title="Resumo do Mês")
    ws2.append(["Indicador", "Quantidade"])
    ws2["A1"].font = Font(bold=True)
    ws2["B1"].font = Font(bold=True)

    total = len(empresas)
    aprovadas = sum(1 for e in empresas if e["status"] in ("Aprovado para Contato", "Contatado", "Fechou"))
    aguardando = sum(1 for e in empresas if e["status"] == "Aguardando Revisão")

    ws2.append(["Total de Empresas no Mês", total])
    ws2.append(["Aguardando Revisão Manual", aguardando])
    ws2.append(["Aprovadas para Contato", aprovadas])
    ws2.append([])
    ws2.append(["Resumo por Módulo", "Total"])

    modulos = conn.execute("SELECT nome FROM modulos").fetchall()
    for m in modulos:
        m_nome = m["nome"]
        qtd_m = sum(1 for e in empresas if m_nome in (e["modulos"] or ""))
        ws2.append([m_nome, qtd_m])

    conn.close()
    wb.save(str(arquivo))
    return arquivo


async def renovar_token_microsoft() -> str:
    refresh_token = get_cfg("ms_refresh_token")
    if not refresh_token:
        return ""

    tenant = os.getenv("AZURE_TENANT_ID", "common")
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "User.Read Files.ReadWrite Calendars.ReadWrite offline_access"
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, data=data)
        if r.status_code == 200:
            tokens = r.json()
            if tokens.get("refresh_token"):
                set_cfg("ms_refresh_token", tokens["refresh_token"])
            return tokens.get("access_token", "")
    return ""


async def sincronizar_onedrive(mes_ref: str = None) -> bool:
    agora = datetime.now()
    if not mes_ref:
        mes_ref = agora.strftime("%m-%Y")

    arquivo_local = gerar_excel_mes(mes_ref)
    access_token = await renovar_token_microsoft()
    if not access_token:
        return False

    mes_num, ano_str = mes_ref.split("-")
    pasta_mes = MESES_PT.get(int(mes_num), f"{mes_num}-Mes")
    caminho_onedrive = f"Vórtice/Investigador/{ano_str}/{pasta_mes}/VOTICE_INVESTIGADOR_{mes_ref}.xlsx"

    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{caminho_onedrive}:/content"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.put(url, headers=headers, content=arquivo_local.read_bytes())
        if r.status_code in (200, 201):
            log_evento("SISTEMA", "OneDrive Sincronizado", caminho_onedrive)
            return True
    return False
