# LLM Wiki Agent

A local-first knowledge base that uses AI to build and maintain a structured wiki from your documents. Drop in a PDF, Word doc, article, or text file — the agent reads it, extracts what matters, and weaves it into an interconnected set of markdown pages that compounds in value as you add more sources.

Based on the [LLM Wiki pattern by Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

---

## What it does

Most AI document tools answer questions and forget everything. This one builds a persistent wiki — a set of structured, interlinked markdown files that grows richer every time you add a source. When you ingest a document the agent creates summary pages, entity pages (people, tools, organisations), concept pages (ideas, methods, frameworks), updates a master index, and cross-links everything. Query it later and it's already done the work.

The wiki is plain markdown files on your filesystem. You own them. No database, no cloud service, no lock-in. Open the wiki folder in Obsidian and you get a full graph view for free.

---

## Files in this package

| File | Purpose |
|------|---------|
| `agent.py` | Main script — all CLI commands live here |
| `providers.py` | LLM provider clients (Ollama, Gemini, Claude, OpenAI) |
| `tools.py` | Filesystem tools the agent uses to read and write the wiki |
| `schema.py` | Generates the AGENTS.md schema file that configures the agent's behaviour |
| `requirements.txt` | Python package dependencies |
| `install.ps1` | Windows setup script (installs all dependencies) |

---

## Installation (Windows)

**1. Open PowerShell as Administrator** (right-click the Start menu, Windows PowerShell (Admin))

**2. Navigate to this folder:**
```powershell
cd C:\path\to\llm-wiki-agent
```

**3. Run the install script:**
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\install.ps1
```

This will install Python, Git, Ollama, and all required Python packages automatically. If you plan to use a cloud provider (Gemini, Claude, or OpenAI) instead of Ollama, you can skip the Ollama install:

```powershell
.\install.ps1 -SkipOllama
```

---

## Setup

**1. Create a new wiki folder and run init:**
```powershell
mkdir C:\my-wiki
cd C:\my-wiki
python C:\path\to\agent.py init
```

This creates a `config.yaml` with all options commented in. Open it in any text editor.

**2. Edit `config.yaml`** — set your wiki's purpose and uncomment one provider block:

```yaml
wiki:
  purpose: "Research wiki for my machine learning studies"

# Uncomment ONE of these:

ollama:                          # Free, runs locally
  model: qwen2.5:14b
  base_url: http://localhost:11434

# gemini:                        # Free tier available
#   model: gemini-2.0-flash
#   api_key: YOUR_KEY_HERE

# anthropic:                     # Best quality for complex wikis
#   model: claude-sonnet-4-5
#   api_key: YOUR_KEY_HERE
```

**3. Run init again** to apply the config and create the wiki structure:
```powershell
python agent.py init
```

---

## Choosing a provider

| Provider | Cost | Quality | Setup |
|----------|------|---------|-------|
| Ollama | Free | Good (depends on model) | Runs locally, needs a capable GPU or CPU |
| Gemini | Free tier available | Good | API key from aistudio.google.com |
| Claude | Pay per use (~$0.05-0.15 per ingest) | Excellent | API key from console.anthropic.com |
| OpenAI | Pay per use | Very good | API key from platform.openai.com |

For **Ollama**, recommended models: `qwen2.5:14b` (best), `qwen2.5:7b` (faster), `llama3.1:8b`. Pull one with:
```powershell
ollama pull qwen2.5:14b
ollama serve
```

For **cloud providers**, generate an API key from the links above and paste it into `config.yaml`. Never commit your API key to git — use environment variables for shared setups:
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GOOGLE_API_KEY = "AIza..."
```

---

## Daily usage

### Ingest a source document
Drop a file into your wiki's `raw/` folder, then:
```powershell
python agent.py ingest raw\my-article.pdf
python agent.py ingest raw\notes.docx
python agent.py ingest raw\paper.txt
```
Supported formats: PDF, Word (.docx), plain text, HTML, CSV, Markdown, JSON, YAML.

The agent will read the document, extract key information, and create or update 5-15 wiki pages automatically.

### Ask a question
```powershell
python agent.py query "What are the main differences between transformers and RNNs?"
python agent.py query "Who are the key researchers in this field?"
```

### Interactive chat session
```powershell
python agent.py chat
```

Inside chat you can have a conversation, ask follow-up questions, request analyses, and ask the agent to ingest files, all while it remembers the context of the session. Special commands:

| Command | What it does |
|---------|-------------|
| `/ingest raw\file.pdf` | Ingest a source file |
| `/query what is X?` | Run a focused query |
| `/lint` | Health-check the wiki |
| `/reset` | Clear conversation history (wiki unchanged) |
| `/exit` | Return to the terminal |

### Health-check the wiki
```powershell
python agent.py lint
```
Checks for orphan pages, broken wikilinks, concepts mentioned without their own page, and contradictions between sources.

---

## Wiki structure

After a few ingests your wiki folder will look like this:

```
my-wiki/
├── AGENTS.md               <- Agent instructions (regenerated by init)
├── config.yaml             <- Your configuration
├── raw/                    <- Source documents (agent never modifies these)
│   ├── paper.pdf
│   └── article.docx
└── wiki/                   <- Everything the agent writes
    ├── index.md            <- Master catalogue of all pages
    ├── log.md              <- Append-only history of all operations
    ├── entities/           <- People, organisations, tools, products
    ├── concepts/           <- Ideas, methods, frameworks, theories
    ├── sources/            <- One summary page per ingested document
    └── synthesis/          <- Analyses, comparisons, and conclusions
```

Pages cross-reference each other using `[[wikilink]]` syntax, compatible with Obsidian. Open the `wiki/` folder as an Obsidian vault to get a visual graph of how everything connects.

---

## Tips

- **Git your wiki.** Run `git init` inside the wiki folder — you get full version history of every page for free. Commit after each session.
- **One source at a time** gives better results than batch ingesting. Stay involved and guide the agent on what to focus on.
- **Ask the agent to save good answers.** After a useful query response say "save that as an analysis page" — good insights should live in the wiki, not disappear into chat history.
- **Reconfigure any time.** Edit `config.yaml` and run `python agent.py init` again to update the wiki's purpose or switch providers. Your existing pages are never touched.
- **Switch providers freely.** The wiki is provider-agnostic markdown. Start with Gemini's free tier, switch to Claude for important ingests, run queries with Ollama — all against the same wiki.

---

## Troubleshooting

**"Config file not found"** — Run `python agent.py init` first to create `config.yaml`.

**"Ollama not reachable"** — Start Ollama with `ollama serve` in a separate terminal, or switch to a cloud provider in `config.yaml`.

**Gemini 429 errors** — The free tier has low quotas. Wait a minute and retry, or add billing to your project at aistudio.google.com to unlock higher limits. The agent will automatically try fallback models before giving up.

**Agent writes very little after ingest** — Try a larger or more capable model. Small models (under 7B parameters) often skip creating cross-reference pages. You can also prompt explicitly: "make sure to create entity and concept pages for everything mentioned".

**Slow responses** — Normal for large local models. Check `ollama ps` to confirm the model is loaded into VRAM. Cloud providers are generally much faster.
