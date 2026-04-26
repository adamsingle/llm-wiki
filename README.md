# LLM Wiki Agent

A local-first, agentic knowledge base powered by [Ollama](https://ollama.com), implementing the persistent wiki pattern described by Andrej Karpathy:
> https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

The core idea: instead of RAG (re-deriving knowledge from raw documents every time you ask a question), the LLM **builds and maintains a persistent wiki** — a structured collection of interlinked markdown pages. Knowledge compounds. Contradictions get flagged. The wiki gets richer with every source you add.

```
                        ┌─────────────────────────────┐
  You drop in sources   │        Wiki Agent            │
  You ask questions ──▶ │   (Ollama + Tool Calling)    │──▶ You read the wiki
  You request lint      │                              │     (Obsidian, VS Code,
                        └──────────┬──────────────────┘      any markdown viewer)
                                   │
              reads (never writes) │ reads + writes
                                   │
                    ┌──────────────▼──────────────┐
                    │   raw/          wiki/        │
                    │  (your         (LLM-owned    │
                    │  sources)       markdown)    │
                    └─────────────────────────────┘
```

---

## Requirements

- **Python 3.9+**
- **[Ollama](https://ollama.com)** running locally
- A tool-calling capable model (see below)

### Recommended Models

| Model | Size | Tool calling | Notes |
|-------|------|-------------|-------|
| `qwen2.5` | 7B | ✅ Excellent | Best overall for this task |
| `qwen2.5:14b` | 14B | ✅ Excellent | Better synthesis, needs more RAM |
| `llama3.1` | 8B | ✅ Good | Good alternative |
| `mistral-nemo` | 12B | ✅ Good | Strong at following instructions |
| `phi4` | 14B | ✅ Good | Very instruction-following |

Pull your chosen model:
```bash
ollama pull qwen2.5
```

---

## Installation

```bash
git clone <this-repo>
cd llm-wiki-agent
pip install -r requirements.txt
```

---

## Quick Start

### 1. Create your config

```bash
python agent.py init
```

This creates a sample `config.yaml`. Open it and edit the `wiki.purpose` and `wiki.domain_description` fields to describe what your wiki is for. This directly shapes how the agent behaves.

### 2. Initialise the wiki

Run init again after editing config.yaml:

```bash
python agent.py init
```

This creates:
```
./wiki/          ← The wiki (agent-maintained markdown)
  index.md       ← Master catalog
  log.md         ← Operation log
  entities/
  concepts/
  sources/
  synthesis/
./raw/           ← Drop your source files here
./AGENTS.md      ← The agent's schema/instructions (generated from config.yaml)
```

### 3. Add a source and ingest it

```bash
# Drop a markdown or text file into raw/
cp ~/Downloads/interesting-article.md raw/

# Ingest it
python agent.py ingest raw/interesting-article.md
```

The agent will read the source, extract insights, create wiki pages, update the index, and log what it did.

### 4. Ask questions

```bash
python agent.py query "What are the main arguments for X?"
```

Or start an interactive session:

```bash
python agent.py chat
```

### 5. Lint the wiki periodically

```bash
python agent.py lint
```

---

## Interactive Chat Commands

Inside `python agent.py chat`:

| Command | What it does |
|---------|-------------|
| `/ingest raw/file.md` | Run the ingest workflow for a source file |
| `/query What is X?` | Run the query workflow (reads wiki, synthesises answer) |
| `/lint` | Run a full wiki health check |
| `/reset` | Start a fresh conversation (keeps the wiki) |
| `/exit` | Exit |
| *(anything else)* | Free-form conversation with the agent |

---

## Using with Obsidian

Obsidian is the recommended way to browse your wiki — it renders wikilinks, shows the graph view, and lets you follow connections visually.

1. Open the `wiki/` folder as an Obsidian vault
2. Install the **Dataview** plugin for querying page frontmatter
3. Use **Graph View** to see what's connected
4. Use **Obsidian Web Clipper** browser extension to clip articles directly to `raw/`

---

## Configuring Your Wiki

The key file is `config.yaml`. Edit it to change:

```yaml
wiki:
  purpose: "Tracking my reading on AI safety and alignment"
  domain_description: |
    This wiki follows papers, articles, and books on AI safety.
    It should highlight: key researchers, competing frameworks, 
    empirical results, and open disagreements in the field.
  page_categories:
    - entities      # Researchers, organisations, models
    - concepts      # Technical concepts, frameworks, arguments
    - sources       # Paper/article summaries
    - synthesis     # Cross-paper analyses and comparisons

ollama:
  model: qwen2.5   # Change to your preferred model
  base_url: http://localhost:11434
```

After editing `config.yaml`, regenerate `AGENTS.md`:
```bash
python agent.py init
```

You can also edit `AGENTS.md` directly to fine-tune the agent's instructions — it's just a markdown file.

---

## Source Formats

The agent can read anything as plain text. For best results, convert sources to markdown first:
- **Web articles**: Use [Obsidian Web Clipper](https://obsidian.md/clipper) or [markdownify](https://github.com/matthewwithanm/python-markdownify)
- **PDFs**: Use `pdftotext` or `pypdf2`
- **Plain text**: Works as-is
- **YouTube transcripts**: Copy and paste into a `.txt` file

---

## Adding a Search Tool (for large wikis)

At ~100+ pages, you may want proper search. The gist recommends [qmd](https://github.com/tobi/qmd) for local hybrid BM25/vector search. The agent's built-in `search_wiki` tool uses basic text matching, which works well at small scale.

---

## Architecture Notes

**Why tool calling, not code execution?**
Tool calling is more reliable with smaller local models than asking them to generate and run arbitrary shell scripts. The tools are simple, predictable, and the agent can't do anything outside the wiki root.

**Why inject index.md at session start?**
The agent has no memory between sessions. By injecting the current wiki index into every system prompt, it always knows what pages exist without having to list them first.

**Why AGENTS.md (not hardcoded prompts)?**
AGENTS.md is user-editable and version-controlled alongside the wiki. As you figure out what conventions work for your domain, you can tune the agent's instructions directly. The schema co-evolves with the wiki.

**Why local/Ollama?**
Privacy, cost, and offline access. Your knowledge base may contain personal or sensitive information. Running locally means nothing leaves your machine.

---

## Troubleshooting

**"Ollama not detected"**
→ Make sure Ollama is running: `ollama serve`

**"Model X not found"**
→ Pull it: `ollama pull qwen2.5`

**Agent doesn't use tools / ignores instructions**
→ Try a larger or different model. qwen2.5:14b is significantly more reliable than 7b for complex multi-tool tasks.

**Agent writes incomplete pages**
→ Small models sometimes stop early. Add to your chat: "Please continue — finish writing the full page."

**Context window errors**
→ Your source file may be too large. Split it into sections and ingest each separately.