import html
import os
import traceback

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


def create_battle_iframe(battle_id: str) -> str:
    """Creates JUST the HTML for the battle iframe tag."""
    print("Creating iframe content for battle ID: ", battle_id)
    battle_url = f"https://play.pokemonshowdown.com/{battle_id}"
    return f"""
    <iframe
        id="battle-iframe"
        class="battle-iframe"
        src="{battle_url}"
        allowfullscreen
    ></iframe>
    """


def create_idle_html(status_message: str, instruction: str) -> str:
    """Creates a visually appealing idle screen HTML fragment."""
    return f"""
    <div class="content-container idle-container">
        <div class="message-box">
            <p class="status">{status_message}</p>
            <p class="instruction">{instruction}</p>
        </div>
    </div>
    """


def create_error_html(error_msg: str) -> str:
    """Creates HTML fragment to display an error message."""
    return f"""
    <div class="content-container error-container">
        <div class="message-box">
            <p class="status">馃毃 Error 馃毃</p>
            <p class="instruction">{error_msg}</p>
        </div>
    </div>
    """


def render_homepage() -> str:
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pokemon Battle Livestream</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;700&family=Press+Start+2P&display=swap" rel="stylesheet">
        <style>
            * { box-sizing: border-box; }
            html, body {
                margin: 0;
                padding: 0;
                height: 100%;
                width: 100%;
                overflow: hidden;
                font-family: 'Poppins', sans-serif;
                color: #ffffff;
                background-color: #1a1a1a;
            }
            #stream-container {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .battle-iframe {
                width: 100%;
                height: 100%;
                border: none;
                display: block;
            }
            .content-container {
                width: 100%;
                height: 100%;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                padding: 20px;
                text-align: center;
            }
            .idle-container {
                background-image: url('/static/pokemon_huggingface.png');
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
            }
            .error-container {
                background: linear-gradient(135deg, #4d0000, #1a0000);
            }
            .message-box {
                background-color: rgba(0, 0, 0, 0.75);
                padding: 40px 50px;
                border-radius: 20px;
                max-width: 70%;
                box-shadow: 0 8px 25px rgba(0, 0, 0, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .status {
                font-family: 'Press Start 2P', cursive;
                font-size: clamp(1.5em, 4vw, 2.5em);
                margin-bottom: 25px;
                color: #ffcb05;
                text-shadow: 3px 3px 0px #3b4cca;
                animation: pulse 2s infinite ease-in-out;
            }
            .instruction {
                font-size: clamp(1em, 2.5vw, 1.4em);
                color: #f0f0f0;
                line-height: 1.6;
                text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.7);
            }
             .instruction strong {
                 color: #ff7f0f;
                 font-weight: 700;
             }
            .error-container .status {
                color: #ff4d4d;
                text-shadow: 2px 2px 0px #800000;
                animation: none;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); opacity: 1; }
                50% { transform: scale(1.03); opacity: 0.9; }
            }
        </style>
    </head>
    <body>
        <div id="stream-container">
             <div class="content-container idle-container">
                <div class="message-box">
                    <p class="status">Initializing...</p>
                    <p class="instruction">Setting up Pokémon Battle Stream</p>
                </div>
            </div>
        </div>
        <script>
            const streamContainer = document.getElementById('stream-container');
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;
            let ws;

            function connectWebSocket() {
                ws = new WebSocket(wsUrl);

                ws.onmessage = function(event) {
                    streamContainer.innerHTML = event.data;
                };

                ws.onclose = function() {
                    streamContainer.innerHTML = `
                        <div class="content-container error-container">
                            <div class="message-box" style="background-color: rgba(0,0,0,0.6);">
                                <p class="status">Disconnected</p>
                                <p class="instruction">Connection to the stream server lost. Attempting to reconnect...</p>
                            </div>
                        </div>`;
                    setTimeout(connectWebSocket, 3000);
                };

                ws.onerror = function(error) {
                    console.error('WebSocket Error:', error);
                     streamContainer.innerHTML = `
                        <div class="content-container error-container">
                            <div class="message-box">
                                <p class="status">Connection Error</p>
                                <p class="instruction">Could not connect to the stream server. Please check the backend.</p>
                            </div>
                        </div>`;
                    ws.close();
                };
            }

            connectWebSocket();
        </script>
    </body>
    </html>
    """


def render_last_action_page(last_action_file: str) -> HTMLResponse:
    file_content_raw = ""
    error_message = None

    try:
        with open(last_action_file, "r", encoding="utf-8") as file:
            file_content_raw = file.read()
    except FileNotFoundError:
        error_message = f"Log file not found: '{last_action_file}'"
        print(f"WARN: {error_message}")
    except Exception as e:
        error_message = f"An unexpected error occurred while reading '{last_action_file}': {e}"
        print(f"ERROR: {error_message}")
        traceback.print_exc()

    display_content = html.escape(file_content_raw) if not error_message else error_message
    content_class = "error" if error_message else "log-content"

    html_output = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Last Action Log</title>
        <style>
             @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;700&family=Press+Start+2P&display=swap');
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            html, body {{ height: 100%; width: 100%; overflow: hidden; }}
            body {{
                font-family: 'Poppins', sans-serif;
                line-height: 1.5;
                padding: 15px;
                background-color: transparent;
                color: #FFFFFF;
                display: flex;
                justify-content: center;
                align-items: center;
                text-align: center;
            }}
            .content-wrapper {{ max-width: 100%; max-height: 100%; }}
            .log-content {{
                font-size: 2em;
                white-space: pre-wrap;
                word-wrap: break-word;
                color: #EAEAEA;
                text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.7);
            }}
            .error {{
                font-family: 'Poppins', sans-serif;
                font-size: 1.6em;
                color: #FFBDBD;
                font-weight: bold;
                background-color: rgba(100, 0, 0, 0.7);
                border: 1px solid #FF8080;
                padding: 10px 15px;
                border-radius: 8px;
                text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.8);
                white-space: normal;
            }}
        </style>
    </head>
    <body>
        <div class="content-wrapper">
             <div class="{content_class}">{display_content}</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_output)


def register_page_routes(app, last_action_file: str) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def get_homepage():
        return render_homepage()

    @app.get("/last_action", response_class=HTMLResponse)
    async def get_last_action_log():
        return render_last_action_page(last_action_file)


def ensure_static_assets(app, static_dir: str) -> None:
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)
        print(f"Created static directory at: {os.path.abspath(static_dir)}")
        print("!!! Please add 'pokemon_huggingface.png' to this directory! !!!")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    print(f"Mounted static directory '{static_dir}' at '/static'")
