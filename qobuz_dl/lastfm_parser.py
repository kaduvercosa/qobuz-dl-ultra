import httpx
from bs4 import BeautifulSoup
from qobuz_dl.color import OFF, GREEN, RED, WARNING as YELLOW, INFO as CYAN


def fetch_lastfm_playlist(url: str) -> list:
    """
    Fetches a Last.fm playlist URL and extracts the tracks.
    Returns a list of dictionaries: [{'artist': '...', 'title': '...'}]
    """
    print(f"{CYAN}[*] Analyzing Last.fm playlist...{OFF}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    html_content = ""
    try:
        # O httpx lança um erro se o status code for inválido (como 600).
        # Usamos uma requisição crua ou passamos um transporte customizado se necessário,
        # mas capturar o erro genérico e tentar via raw transport resolve.
        with httpx.Client(follow_redirects=True) as client:
            # Desativa temporariamente a validação estrita de status se o httpx permitir,
            # ou fazemos a leitura bruta da resposta caso venha o erro 600.
            try:
                response = client.get(url, headers=headers, timeout=15.0)
                html_content = response.text
            except Exception as inner_e:
                # Se o erro for o status code 600 do Nginx que o httpx barrou,
                # tentamos ler a mensagem da exceção se ela trouxer o conteúdo,
                # ou usamos uma abordagem alternativa com o transportador padrão.
                if "600" in str(inner_e) or "status code" in str(inner_e).lower():
                    # Forçamos uma requisição de baixo nível ignorando o parser de
                    # status do httpx
                    req = client.build_request("GET", url, headers=headers)
                    res = client.send(req, timeout=15.0)
                    html_content = res.text
                else:
                    raise inner_e

    except Exception as e:
        print(f"{RED}[!] Failed to connect to Last.fm: {e}{OFF}")
        return []

    # Parse the HTML content
    soup = BeautifulSoup(html_content, "html.parser")
    tracks = []

    # Locate all track rows in the playlist table
    rows = soup.find_all("tr", class_="chartlist-row")

    for row in rows:
        artist_tag = row.find("td", class_="chartlist-artist")
        title_tag = row.find("td", class_="chartlist-name")

        if artist_tag and title_tag:
            artist = artist_tag.get_text(strip=True)
            title = title_tag.get_text(strip=True)
            tracks.append({"artist": artist, "title": title})

    if not tracks:
        print(
            f"{YELLOW}[!] No tracks found. The playlist might be empty, private, or Last.fm blocked the request.{OFF}"
        )
    else:
        print(
            f"{GREEN}[+] Successfully extracted {len(tracks)} tracks from Last.fm!{OFF}"
        )

    return tracks
