import os
import math
import httpx
from typing import List, Dict
from database import get_conn

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY", "")


async def carregar_ibge_se_necessario():
    conn = get_conn()
    qtd = conn.execute("SELECT COUNT(*) as c FROM municipios_ibge").fetchone()["c"]
    if qtd > 5000:
        conn.close()
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get("https://servicodados.ibge.gov.br/api/v1/localidades/municipios")
        municipios = resp.json()

        pop_map = {}
        try:
            r_pop = await client.get(
                "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1/variaveis/9324?localidades=N6[all]"
            )
            dados_pop = r_pop.json()
            series = dados_pop[0]["resultados"][0]["series"]
            for s in series:
                cid_id = int(s["localidade"]["id"])
                val = list(s["serie"].values())[0]
                if val and str(val).isdigit():
                    pop_map[cid_id] = int(val)
        except Exception:
            pass

    cur = conn.cursor()
    for m in municipios:
        cid_id = int(m["id"])
        nome = m["nome"]
        uf = m["microrregiao"]["mesorregiao"]["UF"]["sigla"]
        regiao = m["microrregiao"]["mesorregiao"]["UF"]["regiao"]["nome"]
        meso = m["microrregiao"]["mesorregiao"]["nome"]
        micro = m["microrregiao"]["nome"]
        pop = pop_map.get(cid_id, 0)
        cur.execute(
            """INSERT OR REPLACE INTO municipios_ibge
               (id, nome, uf, regiao, mesorregiao, microrregiao, populacao)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cid_id, nome, uf, regiao, meso, micro, pop)
        )
    conn.commit()
    conn.close()


async def geocodificar_azure(cidade_uf: str):
    if not AZURE_MAPS_KEY:
        return None
    url = "https://atlas.microsoft.com/search/address/json"
    params = {
        "api-version": "1.0",
        "subscription-key": AZURE_MAPS_KEY,
        "query": f"{cidade_uf}, Brasil",
        "countrySet": "BR",
        "limit": 1
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        if r.status_code == 200:
            res = r.json().get("results", [])
            if res:
                pos = res[0]["position"]
                return pos["lat"], pos["lon"]
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def montar_fila_cidades(params: Dict) -> List[Dict]:
    """
    Implementa os 9 modos (A até I) sem listas fixas.
    """
    await carregar_ibge_se_necessario()
    modo = params.get("modo", "estado")
    conn = get_conn()

    if modo == "cidade":
        texto = params.get("cidade_unica", "").strip()
        if not texto:
            conn.close()
            return []
        partes = [p.strip() for p in texto.split("-")]
        nome_cid = partes[0]
        uf_cid = partes[1].upper() if len(partes) > 1 else ""
        query = "SELECT * FROM municipios_ibge WHERE LOWER(nome) = LOWER(?)"
        args = [nome_cid]
        if uf_cid:
            query += " AND UPPER(uf) = ?"
            args.append(uf_cid)
        rows = conn.execute(query + " LIMIT 1", args).fetchall()
        conn.close()
        if rows:
            return [dict(rows[0])]
        return [{"nome": nome_cid, "uf": uf_cid or "BR", "populacao": 0}]

    if modo == "varias":
        linhas = [l.strip() for l in params.get("varias_cidades", "").splitlines() if l.strip()]
        resultado = []
        for linha in linhas:
            partes = [p.strip() for p in linha.split("-")]
            nome_cid = partes[0]
            uf_cid = partes[1].upper() if len(partes) > 1 else ""
            q = "SELECT * FROM municipios_ibge WHERE LOWER(nome) = LOWER(?)"
            a = [nome_cid]
            if uf_cid:
                q += " AND UPPER(uf) = ?"
                a.append(uf_cid)
            r = conn.execute(q + " LIMIT 1", a).fetchone()
            if r:
                resultado.append(dict(r))
            else:
                resultado.append({"nome": nome_cid, "uf": uf_cid or "BR", "populacao": 0})
        conn.close()
        return resultado

    sql = "SELECT * FROM municipios_ibge WHERE 1=1"
    args = []

    if modo in ("estado", "combinada") and params.get("estado"):
        sql += " AND UPPER(uf) = UPPER(?)"
        args.append(params["estado"].strip())

    if modo in ("macro", "combinada") and params.get("macro"):
        sql += " AND LOWER(regiao) = LOWER(?)"
        args.append(params["macro"].strip())

    if modo in ("metro", "combinada") and params.get("metro"):
        termo = f"%{params['metro'].strip()}%"
        sql += " AND (LOWER(mesorregiao) LIKE LOWER(?) OR LOWER(microrregiao) LIKE LOWER(?))"
        args.extend([termo, termo])

    pop_min = params.get("pop_min")
    pop_max = params.get("pop_max")
    if modo in ("pop", "combinada"):
        if pop_min not in (None, ""):
            sql += " AND populacao >= ?"
            args.append(int(pop_min))
        if pop_max not in (None, ""):
            sql += " AND populacao <= ?"
            args.append(int(pop_max))

    sql += " ORDER BY populacao DESC"
    candidatas = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()

    # Filtro de Raio via Azure Maps
    if modo in ("raio", "combinada") and params.get("centro") and params.get("raio_km"):
        coords_centro = await geocodificar_azure(params["centro"])
        raio_km = float(params["raio_km"])
        if coords_centro:
            lat_c, lon_c = coords_centro
            filtradas = []
            # Limita a amostra de candidatos próximos se não houver UF filtrada
            amostra = candidatas[:400] if len(candidatas) > 400 else candidatas
            for c in amostra:
                pos = await geocodificar_azure(f"{c['nome']} - {c['uf']}")
                if pos and haversine_km(lat_c, lon_c, pos[0], pos[1]) <= raio_km:
                    filtradas.append(c)
            candidatas = filtradas

    return candidatas


async def buscar_empresas_azure_maps(cidade: str, uf: str, limite: int = 20) -> List[Dict]:
    """
    Descobre empresas reais na cidade usando EXCLUSIVAMENTE Azure Maps API.
    """
    if not AZURE_MAPS_KEY:
        return []

    coords = await geocodificar_azure(f"{cidade} - {uf}")
    if not coords:
        return []

    lat, lon = coords
    url = "https://atlas.microsoft.com/search/poi/json"
    params = {
        "api-version": "1.0",
        "subscription-key": AZURE_MAPS_KEY,
        "query": "empresa comercio industria servicos",
        "lat": lat,
        "lon": lon,
        "radius": 15000,
        "countrySet": "BR",
        "limit": min(limite, 100)
    }

    empresas = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for tentativa in range(3):
            try:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    for item in r.json().get("results", []):
                        poi = item.get("poi", {})
                        nome = poi.get("name", "").strip()
                        if not nome:
                            continue
                        tel = poi.get("phone", "").strip()
                        site = poi.get("url", "").strip()
                        empresas.append({
                            "nome": nome,
                            "telefone": f"{tel} (Fonte: Azure Maps)" if tel else "NÃO ENCONTRADO",
                            "site_oficial": site if site.startswith("http") else "",
                            "categoria": ", ".join(poi.get("categories", []))
                        })
                    break
            except Exception:
                continue

    return empresas[:limite]
