import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/app/data/vortice.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

MODULOS_OFICIAIS = [
    ("Gestão", "Nota Maps baixa, avaliações sem resposta, Reclame Aqui, dono responde tudo."),
    ("Assessoria Administrativa", "Sem secretária, sem agenda organizada, sem controle de documentos."),
    ("Gestão Empresarial", "Sem controle financeiro, sem estoque, sem processo."),
    ("Suporte Técnico - Informática", "Computador lento, sistema travando, sem backup, impressora com problema."),
    ("Assessoria de TI", "Sem consultoria, sem planejamento de TI, sem segurança, sem email corporativo."),
    ("Gestão de TI", "Sem gestão de computadores, sem rede organizada, sem controle de acessos."),
    ("Gestão de Redes - Redes Sociais", "Instagram parado +30 dias, Facebook abandonado, sem link na bio."),
    ("Gestão de Mídias Sociais", "Sem conteúdo, sem arte, sem vídeo, sem estratégia, TikTok/YouTube parado."),
    ("Uniformes", "Equipe sem padronização em fotos, empresa com equipe grande sem uniforme."),
    ("Brindes Personalizados para Empresa", "Empresa que faz evento, feira, tem loja física e precisa de brinde."),
    ("Roupas e Roupas Estampadas para Empresa", "Empresa com equipe, evento, time que precisa de camiseta, boné, jaleco."),
    ("Usinagem CNC", "Empresa industrial, metalúrgica, oficina que precisa de peça ou usinagem."),
    ("Corte a Laser", "Empresa que precisa de placa, letreiro, MDF, acrílico, brinde cortado, fachada."),
    ("Modelagem 3D", "Empresa que precisa de protótipo, maquete, peça 3D, projeto industrial.")
]

SITES_OFICIAIS = [
    ("Google Maps", "https://maps.google.com", "Mapa", "Nota, avaliações sem resposta, fotos da equipe"),
    ("Google Search", "https://www.google.com", "Outro", "Presença digital e menções públicas"),
    ("Google Reviews", "https://www.google.com", "Avaliação", "Reclamações e falta de resposta"),
    ("Instagram Feed", "https://www.instagram.com", "Rede Social", "Postagens paradas há mais de 30 dias"),
    ("Instagram Comentários", "https://www.instagram.com", "Rede Social", "Dúvidas de clientes sem resposta"),
    ("Facebook Pages", "https://www.facebook.com", "Rede Social", "Página abandonada ou desatualizada"),
    ("Facebook Groups", "https://www.facebook.com", "Rede Social", "Buscas por serviços/fornecedores"),
    ("Facebook Marketplace", "https://www.facebook.com/marketplace", "Marketplace", "Anúncios amadores sem catálogo"),
    ("LinkedIn Empresa", "https://www.linkedin.com", "Rede Social", "Número de funcionários e estrutura"),
    ("LinkedIn Pessoas", "https://www.linkedin.com", "Rede Social", "Identificação real do dono/diretor"),
    ("TikTok", "https://www.tiktok.com", "Rede Social", "Perfil parado ou sem vídeos"),
    ("YouTube", "https://www.youtube.com", "Rede Social", "Canal inativo ou sem vídeos"),
    ("X (Twitter)", "https://x.com", "Rede Social", "Perfil abandonado"),
    ("Reclame Aqui", "https://www.reclameaqui.com.br", "Avaliação", "Reclamações não respondidas"),
    ("Trustpilot", "https://www.trustpilot.com", "Avaliação", "Avaliações negativas"),
    ("TripAdvisor", "https://www.tripadvisor.com.br", "Avaliação", "Gestão de atendimento e nota"),
    ("Glassdoor", "https://www.glassdoor.com.br", "Emprego", "Porte da empresa e gestão interna"),
    ("Yelp", "https://www.yelp.com.br", "Avaliação", "Ficha desatualizada"),
    ("OLX", "https://www.olx.com.br", "Marketplace", "Venda informal ou busca por máquinas"),
    ("Mercado Livre", "https://www.mercadolivre.com.br", "Marketplace", "Reputação e presença em vendas"),
    ("GetNinjas", "https://www.getninjas.com.br", "Marketplace", "Demanda por serviços terceirizados"),
    ("Workana", "https://www.workana.com", "Marketplace", "Pedidos de freelancer/design/TI"),
    ("oHub", "https://www.ohub.com.br", "Marketplace", "Cotações B2B de serviços e indústria"),
    ("Sympla", "https://www.sympla.com.br", "Outro", "Organização de eventos (brindes/camisetas)"),
    ("InfoJobs", "https://www.infojobs.com.br", "Emprego", "Vagas abertas indicando porte e carência"),
    ("Bing Places", "https://www.bing.com/maps", "Mapa", "Telefone oficial, endereço e ficha"),
    ("Apple Maps", "https://maps.apple.com", "Mapa", "Presença e dados básicos"),
    ("Foursquare", "https://foursquare.com", "Mapa", "Dados locais e avaliações"),
    ("GuiaMais / Solutudo", "https://www.solutudo.com.br", "Outro", "Cadastro comercial local"),
    ("TeleListas", "https://www.telelistas.net", "Outro", "Telefone e endereço comercial")
]

PORTES_OFICIAIS = [
    ("MEI / Micro", "Até 10 funcionários", "1 loja, dono faz tudo. Ideal para Gestão, Redes Sociais, Uniformes."),
    ("Pequena (ME / EPP)", "10 a 49 funcionários", "1 a 3 lojas. Ideal para Gestão Empresarial, TI, Brindes."),
    ("Média", "50 a 249 funcionários", "Ideal para Assessoria Administrativa, Gestão de TI, Usinagem, Laser."),
    ("Grande", "+250 funcionários", "Ideal para Modelagem 3D, Usinagem CNC, Gestão Empresarial Completa.")
]


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS config (
        chave TEXT PRIMARY KEY,
        valor TEXT
    );

    CREATE TABLE IF NOT EXISTS modulos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE,
        sinais TEXT,
        ativo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE,
        url_base TEXT,
        tipo TEXT,
        prova_desc TEXT,
        ativo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS portes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE,
        faixa TEXT,
        descricao TEXT,
        ativo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS fila_cidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cidade TEXT,
        uf TEXT,
        populacao INTEGER,
        lat REAL,
        lon REAL,
        status TEXT DEFAULT 'pendente',
        empresas_meta INTEGER DEFAULT 20,
        empresas_feitas INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS empresas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mes_ref TEXT,
        data_investigacao TEXT,
        cidade TEXT,
        populacao TEXT,
        nome TEXT,
        telefone TEXT,
        dono TEXT,
        instagram TEXT,
        porte TEXT,
        score INTEGER,
        modulos TEXT,
        sinais TEXT,
        provas_json TEXT,
        sites_verificados TEXT,
        conclusao TEXT,
        status TEXT DEFAULT 'Aguardando Revisão',
        quem_investigou TEXT,
        filtro_usado TEXT,
        UNIQUE(mes_ref, nome, cidade)
    );

    CREATE TABLE IF NOT EXISTS logs_auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quando TEXT,
        quem TEXT,
        acao TEXT,
        detalhe TEXT
    );

    CREATE TABLE IF NOT EXISTS municipios_ibge (
        id INTEGER PRIMARY KEY,
        nome TEXT,
        uf TEXT,
        regiao TEXT,
        mesorregiao TEXT,
        microrregiao TEXT,
        populacao INTEGER DEFAULT 0
    );
    """)

    for nome, sinais in MODULOS_OFICIAIS:
        cur.execute("INSERT OR IGNORE INTO modulos (nome, sinais, ativo) VALUES (?, ?, 1)", (nome, sinais))

    for nome, url, tipo, desc in SITES_OFICIAIS:
        cur.execute("INSERT OR IGNORE INTO sites (nome, url_base, tipo, prova_desc, ativo) VALUES (?, ?, ?, ?, 1)", (nome, url, tipo, desc))

    for nome, faixa, desc in PORTES_OFICIAIS:
        cur.execute("INSERT OR IGNORE INTO portes (nome, faixa, descricao, ativo) VALUES (?, ?, ?, 1)", (nome, faixa, desc))

    defaults = {
        "worker_status": "parado",
        "regiao_ativa": "Nenhuma",
        "filtros_ativos": "{}",
        "empresas_por_cidade": "20",
        "notif_modo": "email",
        "notif_email": "",
        "notif_whatsapp": "",
        "notif_zap_token": "",
        "notif_webhook": "",
        "ms_refresh_token": ""
    }
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO config (chave, valor) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


def log_evento(quem: str, acao: str, detalhe: str = ""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO logs_auditoria (quando, quem, acao, detalhe) VALUES (?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), quem, acao, detalhe)
    )
    conn.commit()
    conn.close()


def get_cfg(chave: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
    conn.close()
    return row["valor"] if row else default


def set_cfg(chave: str, valor: str):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)", (chave, str(valor)))
    conn.commit()
    conn.close()
