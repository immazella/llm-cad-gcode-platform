# LLM-Based CAD and G-Code Generation, Editing & Validation Platform

A web-based research and educational prototype that connects natural-language CAD modeling with deterministic CAM, machine-specific G-code generation, and rule-based validation.

Describe a part, import a STEP model, select faces in an interactive 3D viewer, and request edits through chat. Generate three-axis milling programs for a selected machine profile, inspect validation findings, and animate the toolpath.

The same CAD/CAM tools are available through an **MCP server**, allowing compatible AI clients to use the application as a tool-based modeling environment.

> **Safety:** This is a prototype, not a certified CAD/CAM or machine-safety system. A report with no detected errors is not proof that a program is safe to run. Review the post-processor, machine configuration, workholding, tooling, and simulation before using any generated program on physical equipment.

## Features

- **Natural-language CAD:** create boxes and cylinders; add holes, pockets, fillets, chamfers, and engraving; modify features and undo edits.
- **STEP import/export:** inspect imported geometry and recognize holes and pockets.
- **Selection-aware editing:** select faces or regions in the 3D viewer and refer to them in chat.
- **Deterministic CAM:** convert a machining plan into a machine-independent toolpath representation, then emit controller-specific G-code.
- **Machine profiles:** example Fanuc, LinuxCNC, and GRBL profiles, with configurable limits, tools, offsets, and machining settings.
- **G-code validation:** check syntax, controller compatibility, travel limits, and basic motion safety; inspect line-level findings.
- **Toolpath visualization:** play, pause, scrub, and adjust playback speed while following the corresponding G-code line.
- **MCP integration:** local stdio, remote HTTP, and an embedded 3D viewer for compatible MCP Apps hosts.
- **Workshop and audit tools:** inspect machine-related information, compare programs, review checklists, and examine tool-call logs.

## Language support

The web application and the embedded MCP viewer are available in **Turkish and English**. Use the **Language** selector in the top bar; the choice is saved in the browser and Turkish is the default.

- The interface, validation reports, rule descriptions, error messages, workshop guidance, and **new** assistant replies follow the selected language.
- Earlier chat messages and audit records stay in the language they were written in; switching does not rewrite them or reset the model.
- G-code, numeric values, identifiers, file names, and user-supplied names are never translated. Generated programs are identical in both languages.
- The API negotiates the language from `Accept-Language` (`tr` or `en`); `/api/chat` also accepts an optional `"language"` field. Clients that send neither get Turkish, so existing MCP integrations keep working unchanged.
- **Offline mode** has a Turkish grammar and a smaller English grammar (for example `create box 60x40x10`, `add hole diameter 6 depth 5 x=20 y=-15`, `generate G-code`). Free-form English needs an online LLM provider.

## Architecture

```text
React + React Three Fiber frontend
                |
           REST API (/api)
                |
FastAPI application <---- MCP server (stdio / HTTP)
                |
          Shared workspace
          /      |       \
  LLM tools     CAD      CAM / validation
                |              |
        CadQuery / OCC    Toolpath IR
                               |
                       Machine post-processor
                               |
                            G-code
```

The LLM orchestrates tools; it does not calculate geometry or decide whether a program passes validation. UI actions, chat commands, and MCP calls share the same workspace implementation. The LLM receives compact model and selection context rather than the full mesh.

## Installation

Prerequisites: **Python 3.10–3.13** (3.12 recommended) and **Node.js 18+**. The startup scripts install backend dependencies into `.venv` and update them when `backend/requirements.txt` changes.

### macOS / Linux

```bash
git clone https://github.com/immazella/llm-cad-gcode-platform.git
cd llm-cad-gcode-platform
./run_mac_linux.sh
```

The first run creates the virtual environment, installs dependencies, and starts the backend and frontend. Open **http://localhost:5173**. Installing CadQuery may take several minutes.

### Windows

After cloning the repository, run:

```bat
run_windows.bat
```

### Manual setup

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

Backend terminal, starting from the repository root:

```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --port 8000
```

Frontend terminal, starting from the repository root:

```bash
cd frontend
npm install
npm run dev
```

For a single-server setup, build the frontend with `cd frontend && npm run build`, then start the backend and open **http://localhost:8000**. FastAPI serves the generated `frontend/dist` directory.

## Connecting an LLM API

The application runs without any API key in **offline mode** (a rule-based command parser). To use natural-language chat, connect an LLM provider. Keys stay on your computer; they are never committed to Git.

### 1. Get an API key

Create a key in your provider's console, for example Anthropic, OpenAI, DeepSeek, or Google AI Studio (Gemini). A local Ollama model needs no key. Provider usage is billed to your own account.

### 2. Create `backend/.env`

From the repository root:

```bash
cp backend/.env.example backend/.env
# Windows: copy backend\.env.example backend\.env
```

`backend/.env` is listed in `.gitignore`. Never put a real key in `.env.example` or any other tracked file.

### 3. Choose one provider

Open `backend/.env` in a text editor and keep **one** block. Replace the placeholder with your key, without quotes or spaces around `=`.

**Anthropic Claude**

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-anthropic-key
ANTHROPIC_MODEL=claude-sonnet-5-5
```

**OpenAI**

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-4.1-mini
```

**DeepSeek**

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-deepseek-key
DEEPSEEK_MODEL=deepseek-chat
```

**Google Gemini (OpenAI-compatible endpoint)**

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-gemini-key
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_MODEL=gemini-2.5-flash
```

**Local Ollama** (choose a model that supports tool calling)

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

Other OpenAI-compatible services (Groq, OpenRouter, and similar) use the same `LLM_PROVIDER=openai` pattern with their own `OPENAI_BASE_URL` and `OPENAI_MODEL`. The model must support **tool/function calling**; the assistant works by calling CAD/CAM tools.

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `anthropic`, `openai`, `deepseek`, or `offline`. If omitted, the first key found among Anthropic, OpenAI, and DeepSeek selects the provider. |
| `*_API_KEY` | Your provider key. |
| `*_MODEL` | Optional model name; the defaults are the values shown above. Pick a model your account can access. |
| `OPENAI_BASE_URL` | Endpoint for OpenAI-compatible services. |
| `DEEPSEEK_BASE_URL` | Optional DeepSeek endpoint (default `https://api.deepseek.com`). |

Environment variables already set in your shell take precedence over `backend/.env`, which is useful for CI or temporary keys:

```bash
export LLM_PROVIDER=openai OPENAI_API_KEY=your-openai-key
./run_mac_linux.sh
```

### 4. Restart and verify

The backend reads `backend/.env` only at startup. Stop the app (Ctrl+C) and start it again with `./run_mac_linux.sh` or `run_windows.bat`.

The badge in the top-right corner shows the active provider and model. If it still shows **Offline**, check the provider name, key, and file location, then restart. A rejected key or unavailable model is reported in the chat as an LLM service error; the app keeps running.

The badge is also a button: click it to switch between the configured provider and offline mode without editing `.env`.

### MCP clients

When the tools are used from Claude Desktop, Claude Code, Cursor, or another MCP client, that client's own model does the orchestration. **No key is needed in `backend/.env` for MCP use** (see [MCP integration](#mcp-integration)).

### Offline mode

Without a key the app automatically uses a small rule-based parser in `backend/app/llm/offline.py` (Turkish) and `backend/app/llm/offline_en.py` (English). It is not an LLM; it lets you demonstrate CAD/CAM operations without API access. Set `LLM_PROVIDER=offline` to force this mode even when keys are present.

### Credentials and repository hygiene

- Keep real credentials only in your local environment or `backend/.env`, never in `.env.example`.
- Local environment files, runtime sessions, backups, archives, dependencies, and build outputs are excluded from Git.
- If a credential has been exposed, revoke it through its provider; deleting the file does not revoke the credential.
- Review the staged files before committing. With Gitleaks installed, run:

  ```bash
  gitleaks git --pre-commit --staged --redact
  gitleaks git --redact
  ```

## MCP integration

### Local clients

The MCP server exposes the same tools as the web application. In this mode, the client supplies the model orchestration; the server does not require its own LLM API key.

Generate configuration with paths for your machine:

```bash
cd backend
../.venv/bin/python -m app.mcp_server --print-config
```

- **Claude Desktop:** add the generated server entry to `mcpServers` in `claude_desktop_config.json`, then restart the client.
- **Cursor:** add the generated configuration to `~/.cursor/mcp.json`.
- **Claude Code:** run the generated `claude mcp add` command.

Existing MCP identifiers such as `busproje-cad` and the `ui://busproje/viewer.html` resource are retained for compatibility; they are not the repository's display name.

### Operating modes

| Setting | Behavior |
|---|---|
| `MCP_MODE=auto` | Uses the running web backend when available; otherwise runs the workspace locally. |
| `MCP_MODE=http` | Uses the web backend. Set `CAD_API_URL` for a non-default backend address. |
| `MCP_MODE=local` | Runs the workspace inside the MCP process and persists the session locally. |

When connected to the web backend, MCP edits are reflected in the browser and the model can inspect the user's current face selection. Local sessions use `backend/data/session.pkl` by default.

### Remote clients: one-time link

Web-based clients such as claude.ai or ChatGPT cannot start a process on your computer, so they cannot use the local stdio server. `run_mcp_remote.sh` gives them a temporary public HTTPS address instead.

> **The link is one-time.** It is valid for a single run of the script and stops working as soon as the script exits. Every new run produces a **new address and a new secret token**, so the connector in your client must be updated each time. It is meant for demos and short sessions, not for permanent hosting.

**Prerequisites**

- Run `./run_mac_linux.sh` once so `.venv` and the backend dependencies exist.
- Install [cloudflared](https://developers.cloudflare.com/tunnel/downloads/) (`brew install cloudflared` on macOS). No Cloudflare account is needed.

**Create the link**

```bash
./run_mcp_remote.sh
```

The script performs these steps:

1. **Checks the environment.** It stops if `cloudflared` or `.venv` is missing, installs changed backend requirements, and refuses to start if the port (default `8765`) is already in use.
2. **Generates a secret token.** Unless you set `MCP_TOKEN`, it creates a random 32-character URL-safe token with Python's `secrets.token_urlsafe(24)`. Tokens shorter than 16 characters are rejected.
3. **Starts the MCP server on localhost only.** `python -m app.mcp_server --http` listens on `127.0.0.1:<port>` and serves MCP only at the path `/mcp/<token>`. Requests to any other path, including a wrong token, get `404`.
4. **Opens a Cloudflare Quick Tunnel.** `cloudflared tunnel --url http://127.0.0.1:<port>` creates a random `https://<random-name>.trycloudflare.com` address that forwards to the local server. Your computer opens the connection outward, so no router or firewall changes are required.
5. **Prints the link** once the tunnel address appears:

   ```text
   https://<random-name>.trycloudflare.com/mcp/<secret-token>
   ```

6. **Keeps both processes alive** until you press **Ctrl+C**, or until either process stops. On exit it terminates the server and the tunnel and deletes the temporary log. From that moment the link is dead.

**Connect a client**

Add the printed URL in your client as a remote MCP server (often called a *custom connector*), for example in claude.ai or ChatGPT. The tunnel's DNS record can take a few seconds to propagate; if the first attempt fails, wait briefly and retry. After restarting the script, replace the old URL with the new one and refresh the connector so the client reloads the tool list.

**Optional settings**

| Variable | Effect |
|---|---|
| `MCP_HTTP_PORT` | Local port (default `8765`), e.g. `MCP_HTTP_PORT=8766 ./run_mcp_remote.sh`. |
| `MCP_TOKEN` | Use your own token (at least 16 characters) instead of a random one. The tunnel address still changes on every run. |
| `MCP_LOG_LEVEL=INFO` | Log each tool call and its duration to the terminal. |

**Security and limits**

- **The token in the URL is the only access control.** Anyone who has the complete link can call the tools while the script runs. Do not post it publicly, and stop the script when you are done.
- This is not OAuth or user authentication; it is a short-lived demo connection.
- Tools that touch files on your computer (`load_step`, `save_gcode`, `add_engraving_svg`, and any `path` argument) are hidden and rejected in remote mode. Upload STEP files through the web UI and send G-code as text.
- No LLM key is used by the server in this mode: the remote client's own model calls the tools.
- If the web application is also running (`./run_mac_linux.sh` in another terminal), the remote model works on the same live model and its changes appear in the browser's 3D view. Otherwise the workspace runs inside the MCP process.
- Quick Tunnels do not support SSE, so the server uses stateless Streamable HTTP with plain JSON responses. Cloudflare gives no uptime guarantee for Quick Tunnels; a permanent address requires a Cloudflare account and a named tunnel.
- There is no Windows wrapper. Use WSL, or run the two commands manually:

  ```bash
  cd backend
  MCP_TOKEN=<your-long-random-token> ../.venv/bin/python -m app.mcp_server --http
  # in a second terminal:
  cloudflared tunnel --url http://127.0.0.1:8765
  ```

  Then combine the printed `trycloudflare.com` address with `/mcp/<your-long-random-token>`.

### MCP-specific tools

| Tool | Purpose |
|---|---|
| `show_model_3d` | Open an interactive model viewer in a compatible MCP Apps host. |
| `set_selection` | Set or clear the selected face. |
| `get_context`, `get_selection` | Read model, machine, G-code, and selection context. |
| `render_model`, `render_toolpath` | Produce model or toolpath images. |
| `get_gcode` | Read paginated G-code with line numbers. |
| `load_step`, `upload_gcode`, `add_engraving_svg` | Import supported content; local-file access is restricted remotely. |
| `save_gcode`, `export_step` | Save output in local mode; existing files are not overwritten by default. |
| `list_validation_rules` | List validation rules. |

The embedded viewer's source lives in `mcp_ui/`. Its single-file build is committed at `backend/app/mcp_ui/index.html` so clients can load it without a separate frontend server. Rebuild after changing the viewer:

```bash
cd mcp_ui
npm ci
npm run build
```

Viewer behavior depends on the client's MCP Apps support. Refresh the client's connector after changing exposed tools or resources. Set `MCP_LOG_LEVEL=INFO` to log tool timing to stderr. The server also exposes its modeling instructions through the `cad_asistani` prompt.

## Example workflows

| Workflow | Example |
|---|---|
| Text-to-CAD | “Create a 60 × 60 × 10 mm plate with a central hole of radius 10 mm.” |
| Import | Load `examples/ornek_flans.step` and inspect recognized features. |
| Edit | “Increase the central hole diameter from 20 mm to 30 mm.” |
| Selection-aware edit | Select a hole face, then ask “Set the selected hole radius to 15 mm.” |
| Engraving | Add text, a polyline, or an SVG outline on an upward-facing surface. |
| Machine selection | Choose a profile in the G-code panel or ask the assistant to switch machines. |
| Generate | “Generate G-code for Fanuc.” Review the machining plan, warnings, and validation report. |
| Validate | Load `examples/hatali_ornek.nc` and inspect the highlighted findings. |
| Animate | Open the toolpath view and use playback controls to inspect the program. |

For offline demonstrations, use the offline grammar of the selected language, for example `60x60x10 plaka oluştur` in Turkish or `create box 60x60x10` in English, then `G-code üret` / `generate G-code`.

### Coordinate conventions

- Geometry uses **millimeters**. Parts are centered in XY, with the bottom at Z=0 and the top at Z=height.
- The G-code work origin is the **center of the stock's top face**; cutting depths are negative Z.
- Axis travel limits are checked in machine coordinates using the work offset and, where active, tool-length compensation.
- Configure work offsets and individual tool lengths in the machining settings. Tool numbers remain stable when other tools are added or removed.

## CAD, CAM, and validation

### CAD

The model is a base solid plus an ordered feature tree. Edits rebuild the model atomically; failed edits preserve the previous state. Feature associations use geometric matching rather than relying on OCC face order. Each B-rep face is tessellated separately for selection, and stale selections are rejected using the model version.

Hole and pocket recognition works on both parametric and imported STEP models. Recognized STEP holes can be removed with OCC defeaturing and recreated at a different size. Engraving supports centerline text, font outlines, polylines, and SVG paths on upward-facing horizontal surfaces.

### Machine profiles

Profiles in `backend/profiles/` define controller dialect, supported codes, travel and speed limits, tool-changing behavior, drilling-cycle support, and default setup values.

| Profile | Controller family | Key differences |
|---|---|---|
| `fanuc_vmc` | Fanuc-style VMC | Program numbers, M6, G43 H, and canned drilling cycles. |
| `linuxcnc_mill` | LinuxCNC | Controller-specific formatting, path control, and machine limits. |
| `grbl_router` | GRBL | Expanded drilling moves and manual tool changes; no G81/G83 or G43 H. |

These are **example profiles**, not verified specifications for a particular physical machine. Configure them from the machine's actual documentation.

### CAM pipeline

```text
CAD model → machining plan → toolpath IR → post-processor → G-code
```

The intermediate representation includes rapid/linear/arc moves, drilling cycles, tool changes, spindle commands, and coolant commands. Post-processors adapt those operations to the target controller.

Supported operations include drilling, peck drilling, helical boring, pocketing, engraving, facing, and outer contours. Tool choice and cutting parameters come from the machining configuration. Generation can reduce speeds and feeds to profile limits and report warnings; this does not replace engineering review.

### Validation

Validation is deterministic and profile-aware. Each finding includes a rule code, severity, level, line reference, message, suggestion, and structured data. The rule catalog is available at `GET /api/rules`.

| Level | Checks |
|---|---|
| Syntax and commands | Malformed syntax, unsupported G/M codes, modal conflicts, repeated addresses, missing feed/spindle/tool parameters, incomplete drilling cycles, arc parameters, and program termination. |
| Machine compatibility | Axis travel, feed/spindle limits, tool-changer capacity, cycle support, tool-length compensation, and controller-specific program requirements. |
| Basic motion safety | Cutting with the spindle stopped, unsafe rapid moves into stock, low tool changes, and end-of-program spindle state. |

Reports also include timing estimates and explicit limitations. **Holder/fixture collision detection, material-removal simulation, full CAD comparison, acceleration/look-ahead, macros, and cutter compensation are not fully modeled.** An LLM may explain the findings, but the validator determines them.

## Project structure

```text
backend/
  app/
    main.py              FastAPI endpoints and MCP bridge
    mcp_server.py        MCP stdio/HTTP server
    mcp_ui/index.html    Bundled MCP Apps viewer
    workspace.py         Shared modeling and machining tools
    cad/                 Geometry, features, recognition, engraving
    cam/                 Toolpath generation, profiles, validation
    cam/posts/           Machine-specific post-processors
    llm/                 Tool orchestration and offline parser
  profiles/              Example machine profiles
  alarms/                Controller alarm data
  tests/                 Backend and MCP tests
  requirements.txt
  .env.example
frontend/                Web application (React + Three.js)
mcp_ui/                  Embedded MCP viewer source
examples/                Sample STEP and G-code files
run_mac_linux.sh         Web application launcher
run_windows.bat         Windows launcher
run_mcp_remote.sh        Remote MCP demo launcher
```

## Development and tests

From the repository root:

```bash
cd backend
../.venv/bin/python -m pytest -q
```

On Windows, use `..\.venv\Scripts\python -m pytest -q` from `backend`.

Test and build the web frontend and the MCP viewer:

```bash
cd frontend
npm ci
npm test
npm run build

cd ../mcp_ui
npm ci
npm test
npm run build
```

Run tests in the project's own virtual environment. An inherited `PYTHONPATH` can pull in unrelated packages; local `.env` model settings can also affect tests that assert default model names. The verified macOS test invocation is:

```bash
cd backend
env -u PYTHONPATH DEEPSEEK_MODEL=deepseek-chat ../.venv/bin/python -m pytest -q
```

## Scope and limitations

- Designed for research, education, and demonstrations, not unattended machining or multi-user production hosting.
- CAM focuses on three-axis, top-down machining. Arbitrary 3D surface machining, side-hole machining, and complete workholding analysis are outside its scope.
- STEP editing is limited compared with native parametric modeling; recognized holes have a dedicated editing path.
- Engraving is limited to upward-facing horizontal surfaces. SVG text must be converted to paths; filled regions are not automatically pocketed. The CAD groove representation is an approximation.
- Some accepted controller commands are not geometrically simulated. Unsupported or unmodeled behavior is reported rather than treated as proven safe.
- Basic stock checks do not model the evolving removed material or full holder/fixture geometry.
- Remote MCP access uses a temporary token-bearing tunnel URL, not production authentication. Embedded viewer support varies by client; toolpath playback is provided in the web UI.
- Potential extensions include approval-based G-code patches, material-removal simulation, fixture collision checks, and more realistic timing models.
