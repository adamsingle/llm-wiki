#!/usr/bin/env python3
"""
LLM Wiki Agent
==============
A local-first knowledge base agent using Ollama, implementing the
persistent wiki pattern described by Karpathy:
https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

Usage:
  python agent.py init              # Set up a new wiki
  python agent.py chat              # Open interactive session
  python agent.py ingest <file>     # Ingest a specific source file
  python agent.py query "<question>"  # Ask a one-shot question
  python agent.py lint              # Health-check the wiki
"""

import os
import sys
import json
import yaml
import argparse
import datetime
import textwrap
import requests
from pathlib import Path
from typing import Optional

from tools import WikiTools
from schema import generate_agents_md
from providers import create_provider

# ─── Defaults ────────────────────────────────────────────────────────────────

CONFIG_FILE = "config.yaml"
DEFAULT_MODEL = "gemma4:26b"
OLLAMA_URL = "http://localhost:11434"
MAX_TOOL_ROUNDS = 20   # Safety limit per agent turn

# ─── Ollama Client ────────────────────────────────────────────────────────────

class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, messages: list, tools: list = None, stream: bool = False) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list:
        r = requests.get(f"{self.base_url}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]


# ─── Tool Schema (for Ollama) ──────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file. Use for reading wiki pages, "
                "the index, the log, or raw source documents. "
                "Paths are relative to the wiki root directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to wiki root, e.g. 'wiki/index.md' or 'raw/article.md'"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a wiki page. Always use full markdown with YAML frontmatter. "
                "Use for creating new wiki pages or updating existing ones. "
                "Do NOT use for raw sources — those are read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path under wiki/, e.g. 'wiki/entities/Alice.md'"
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content of the file, including YAML frontmatter"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_wiki",
            "description": "List all pages currently in the wiki directory, with their paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subdirectory": {
                        "type": "string",
                        "description": "Optional subdirectory to list, e.g. 'wiki/entities'. Defaults to all wiki pages."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_raw_sources",
            "description": "List all files in the raw sources directory.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": (
                "Search the wiki for pages containing a keyword or phrase. "
                "Returns a list of matching files with the relevant lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or phrase to look for across all wiki pages"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_log",
            "description": (
                "Append a timestamped entry to the wiki log (wiki/log.md). "
                "Call this after every ingest, significant query answer, or lint pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "Operation type: 'ingest', 'query', 'lint', or 'update'",
                        "enum": ["ingest", "query", "lint", "update"]
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the log entry, e.g. source title or query text"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was done: pages created/updated, key findings, etc."
                    }
                },
                "required": ["operation", "title", "summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a wiki page. Use carefully — only for removing truly orphaned or duplicate pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the wiki file to delete, e.g. 'wiki/old-page.md'"
                    }
                },
                "required": ["path"]
            }
        }
    },
]


# ─── Agent ────────────────────────────────────────────────────────────────────

class WikiAgent:
    def __init__(self, config: dict):
        self.config = config
        self.provider = create_provider(config)
        wiki_root = Path(config.get("paths", {}).get("wiki_root", "."))
        self.tools = WikiTools(wiki_root)
        self.conversation_history: list = []

    # ── Context building ──────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Load AGENTS.md as the system prompt, injecting current index if present."""
        agents_md_path = self.tools.wiki_root / "AGENTS.md"
        if not agents_md_path.exists():
            raise FileNotFoundError(
                "AGENTS.md not found. Run 'python agent.py init' first."
            )
        agents_md = agents_md_path.read_text(encoding="utf-8")

        # Inject current index snapshot so the agent knows what's in the wiki
        index_path = self.tools.wiki_root / "wiki" / "index.md"
        if index_path.exists():
            index_content = index_path.read_text(encoding="utf-8")
            index_section = f"\n\n---\n## CURRENT WIKI INDEX (auto-injected at session start)\n\n{index_content}\n"
        else:
            index_section = "\n\n---\n## CURRENT WIKI INDEX\n\n*(Wiki is empty — no pages yet.)*\n"

        return agents_md + index_section

    # ── Tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, args: dict) -> str:
        """Dispatch a tool call to WikiTools and return a string result."""
        try:
            if name == "read_file":
                return self.tools.read_file(args["path"])
            elif name == "write_file":
                return self.tools.write_file(args["path"], args["content"])
            elif name == "list_wiki":
                return self.tools.list_wiki(args.get("subdirectory"))
            elif name == "list_raw_sources":
                return self.tools.list_raw_sources()
            elif name == "search_wiki":
                return self.tools.search_wiki(args["query"])
            elif name == "append_log":
                return self.tools.append_log(
                    args["operation"], args["title"], args["summary"]
                )
            elif name == "delete_file":
                return self.tools.delete_file(args["path"])
            else:
                return f"ERROR: Unknown tool '{name}'"
        except Exception as e:
            return f"ERROR executing {name}: {e}"

    # ── Agent loop ────────────────────────────────────────────────────────────

    def _run_agent_loop(self, user_message: str) -> str:
        """
        Runs the full agentic loop:
        1. Append user message to conversation
        2. Call LLM (with tools)
        3. If tool calls → execute them, feed results back, repeat
        4. Once no more tool calls → return final text response
        """
        self.conversation_history.append({
            "role": "user",
            "content": user_message,
        })

        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1

            response = self.provider.chat(
                messages=self.conversation_history,
                tools=TOOL_DEFINITIONS,
            )

            message = response["message"]
            # Normalise tool_calls to always include an id
            if message.get("tool_calls"):
                for tc in message["tool_calls"]:
                    if "id" not in tc:
                        tc["id"] = f"call_{tc.get('function', {}).get('name', 'tool')}_{len(self.conversation_history)}"
            self.conversation_history.append(message)

            # Check for tool calls
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                # No tool calls → final answer
                return message.get("content", "").strip()

            # Execute each tool call and add results
            for tc in tool_calls:
                fn = tc.get("function", tc)
                tool_name = fn["name"]
                tool_args = fn.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}

                tool_call_id = tc.get("id", f"call_{tool_name}")

                print(f"  🔧 {tool_name}({', '.join(f'{k}={repr(v)[:60]}' for k,v in tool_args.items())})")

                result = self._execute_tool(tool_name, tool_args)

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(result),  # force string — Gemini rejects non-strings
                })

        return "⚠️ Agent hit maximum tool rounds without finishing. Try a simpler request."

    # ── Public interface ──────────────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        system_prompt = self._build_system_prompt()
        if not self.conversation_history:
            self.conversation_history = [
                {"role": "system", "content": system_prompt}
            ]
        return self._run_agent_loop(user_input)

    def ingest(self, source_path: str) -> str:
        """Convenience: triggers ingest workflow for a specific source."""
        msg = (
            f"Please ingest the source file at: {source_path}\n\n"
            "Use the read_file tool to read it — the tool will automatically extract "
            "text from PDFs, Word docs, HTML, CSV, and plain text files, returning "
            "clean text regardless of the original format.\n\n"
            "Follow the full ingest workflow:\n"
            "1. Read the file with read_file\n"
            "2. Identify the key entities, concepts, and insights\n"
            "3. Write a summary page in wiki/sources/\n"
            "4. Create or update relevant pages in wiki/entities/ and wiki/concepts/\n"
            "5. Update wiki/index.md\n"
            "6. Append to log.md with append_log\n\n"
            "The source content is plain text — write wiki pages in clean markdown."
        )
        return self.chat(msg)

    def query(self, question: str) -> str:
        """Convenience: triggers query workflow for a question."""
        msg = (
            f"Please answer this question using the wiki:\n\n{question}\n\n"
            "Read index.md first, then find and read relevant pages, then synthesize an answer. "
            "If the answer is substantial enough to be useful later, save it as a new wiki page."
        )
        return self.chat(msg)

    def lint(self) -> str:
        """Convenience: triggers lint/health-check workflow."""
        msg = (
            "Please perform a full wiki lint pass:\n"
            "1. List all wiki pages\n"
            "2. Check for orphan pages (no inbound links)\n"
            "3. Look for contradictions between pages\n"
            "4. Identify concepts mentioned but lacking their own page\n"
            "5. Check for missing cross-references\n"
            "6. Summarise findings and make any fixes\n"
            "7. Append a lint entry to log.md"
        )
        return self.chat(msg)

    def reset_conversation(self):
        """Start a fresh conversation (new session, keeps wiki state)."""
        self.conversation_history = []


# ─── CLI ──────────────────────────────────────────────────────────────────────

def load_config(path: str = CONFIG_FILE) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"❌ Config file not found: {path}")
        print("   Run 'python agent.py init' to set up a new wiki.")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if config is None:
        print(f"❌ config.yaml appears to be empty or contains only comments.")
        print("   Delete it and run 'python agent.py init' to regenerate it.")
        sys.exit(1)
    return config


def cmd_init(args):
    """Initialise a new wiki from config.yaml (or create a sample one)."""
    config_path = Path(CONFIG_FILE)

    if not config_path.exists():
        print("No config.yaml found — creating a sample configuration...")
        sample_config = (
            "# LLM Wiki Agent Configuration\n"
            "# Edit this file, then run 'python agent.py init' again to apply.\n"
            "\n"
            "wiki:\n"
            "  purpose: \"A personal knowledge base for learning and research\"\n"
            "  domain_description: |\n"
            "    This wiki tracks articles, books, papers, and notes I read.\n"
            "    It organises knowledge by entities (people, organisations, tools)\n"
            "    and concepts (ideas, methods, frameworks). The wiki should:\n"
            "    - Highlight connections and contradictions between sources\n"
            "    - Build up a running synthesis as new material is added\n"
            "    - Flag open questions and gaps for further research\n"
            "  page_categories:\n"
            "    - entities\n"
            "    - concepts\n"
            "    - sources\n"
            "    - synthesis\n"
            "    - questions\n"
            "\n"
            "# Uncomment ONE provider block and fill in the details.\n"
            "\n"
            "ollama:\n"
            "  model: gemma4:26b\n"
            "  base_url: http://localhost:11434\n"
            "\n"
            "# anthropic:\n"
            "#   model: claude-sonnet-4-5\n"
            "#   api_key:\n"
            "\n"
            "# openai:\n"
            "#   model: gpt-4o\n"
            "#   api_key:\n"
            "\n"
            "# gemini:\n"
            "#   model: gemini-2.0-flash\n"
            "#   api_key:\n"
            "\n"
            "paths:\n"
            "  wiki_root: .\n"
        )
        config_path.write_text(sample_config, encoding="utf-8")
        print(f"✅ Created {CONFIG_FILE}. Edit it to set your wiki's purpose, then run init again.")
        return

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # Create directory structure
    wiki_root = Path(config.get("paths", {}).get("wiki_root", "."))
    dirs = [
        wiki_root / "wiki",
        wiki_root / "wiki" / "entities",
        wiki_root / "wiki" / "concepts",
        wiki_root / "wiki" / "sources",
        wiki_root / "wiki" / "synthesis",
        wiki_root / "raw",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  📁 {d}")

    # Generate AGENTS.md
    agents_md = generate_agents_md(config)
    agents_path = wiki_root / "AGENTS.md"
    agents_path.write_text(agents_md, encoding="utf-8")
    print(f"  📄 {agents_path}")

    # Create empty index.md
    index_path = wiki_root / "wiki" / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "---\ntitle: Wiki Index\nupdated: " + datetime.date.today().isoformat() + "\n---\n\n"
            "# Wiki Index\n\n*No pages yet. Start by ingesting a source.*\n\n"
            "## Entities\n\n## Concepts\n\n## Sources\n\n## Synthesis\n",
            encoding="utf-8"
        )
        print(f"  📄 {index_path}")

    # Create empty log.md
    log_path = wiki_root / "wiki" / "log.md"
    if not log_path.exists():
        log_path.write_text(
            "# Wiki Log\n\nAppend-only record of all operations.\n\n"
            f"## [{datetime.date.today().isoformat()}] init | Wiki initialised\n\n"
            f"Wiki created with purpose: {config['wiki']['purpose']}\n",
            encoding="utf-8"
        )
        print(f"  📄 {log_path}")

    # Verify provider
    from providers import create_provider
    provider = create_provider(config)
    provider_name = (
        "Anthropic" if "anthropic" in config else
        "OpenAI"    if "openai"    in config else
        "Gemini"    if "gemini"    in config else
        "Ollama"
    )
    print()
    if provider.is_available():
        models = provider.list_models()
        if models:
            print(f"✅ {provider_name} is reachable. Available models: {', '.join(models[:5])}")
        else:
            print(f"✅ {provider_name} is reachable.")
    else:
        if "anthropic" in config:
            print("⚠️  Anthropic API key not found.")
            print("   Set the ANTHROPIC_API_KEY environment variable or add api_key to config.yaml.")
        elif "openai" in config:
            print("⚠️  OpenAI API key not found.")
            print("   Set the OPENAI_API_KEY environment variable or add api_key to config.yaml.")
        elif "gemini" in config:
            print("⚠️  Google API key not found.")
            print("   Set the GOOGLE_API_KEY environment variable or add api_key to config.yaml.")
        else:
            print("⚠️  Ollama not reachable at", config.get("ollama", {}).get("base_url", OLLAMA_URL))
            print("   Make sure Ollama is running: https://ollama.com")

    print()
    print("✅ Wiki initialised! Ready to use.")
    print("   Drop source files into ./raw/ then run: python agent.py ingest raw/<file>")
    print("   Or start an interactive session: python agent.py chat")


def cmd_chat(args):
    """Interactive chat session."""
    config = load_config()
    agent = WikiAgent(config)

    from providers import create_provider
    provider = create_provider(config)
    purpose = config.get("wiki", {}).get("purpose", "")
    print(f"\n🌐 LLM Wiki Agent  |  model: {provider.model}")
    print(f"   Purpose: {purpose}")
    print("   Commands: /ingest <file>, /query <question>, /lint, /reset, /exit\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit", "q"):
            print("Goodbye.")
            break
        elif user_input == "/reset":
            agent.reset_conversation()
            print("🔄 Conversation reset.\n")
            continue
        elif user_input == "/lint":
            try:
                user_input = None
                print("🔍 Running wiki lint...\n")
                response = agent.lint()
            except RuntimeError as e:
                print(f"\n⚠️  Error: {e}\n")
                print("Your conversation history is intact — try again or type /reset\n")
        elif user_input.startswith("/ingest "):
            try:
                source = user_input[8:].strip()
                print(f"📥 Ingesting: {source}\n")
                response = agent.ingest(source)
            except RuntimeError as e:
                print(f"\n⚠️  Error: {e}\n")
                print("Your conversation history is intact — try again or type /reset\n")
        elif user_input.startswith("/query "):
            try:
                question = user_input[7:].strip()
                print(f"🔍 Querying: {question}\n")
                response = agent.query(question)
            except RuntimeError as e:
                print(f"\n⚠️  Error: {e}\n")
                print("Your conversation history is intact — try again or type /reset\n")
        else:
            try:
                response = agent.chat(user_input)
                print(f"\nAgent: {response}\n")
            except RuntimeError as e:
                print(f"\n⚠️  Error: {e}\n")
                print("Your conversation history is intact — try again or type /reset\n")


def cmd_ingest(args):
    import shutil
    config = load_config()

    source = Path(args.file)
    if not source.exists():
        print(f"❌ File not found: {args.file}")
        sys.exit(1)

    # Copy into raw/ so the agent always works from a stable relative path
    wiki_root = Path(config.get("paths", {}).get("wiki_root", "."))
    raw_dir = wiki_root / "raw"
    raw_dir.mkdir(exist_ok=True)
    dest = raw_dir / source.name
    if not dest.exists():
        shutil.copy2(source, dest)
        print(f"  📥 Copied {source.name} → raw/{source.name}")
    else:
        print(f"  ℹ  raw/{source.name} already exists")

    agent = WikiAgent(config)
    print(f"\n📥 Ingesting: raw/{source.name}\n")
    response = agent.ingest(f"raw/{source.name}")
    print(f"\n{response}\n")


def cmd_query(args):
    config = load_config()
    agent = WikiAgent(config)
    print(f"🔍 Query: {args.question}\n")
    response = agent.query(args.question)
    print(f"\n{response}\n")


def cmd_lint(args):
    config = load_config()
    agent = WikiAgent(config)
    print("🔍 Running wiki lint...\n")
    response = agent.lint()
    print(f"\n{response}\n")


def main():
    parser = argparse.ArgumentParser(
        description="LLM Wiki Agent — local knowledge base powered by Ollama"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialise a new wiki (or create sample config)")
    subparsers.add_parser("chat", help="Start an interactive session")

    p_ingest = subparsers.add_parser("ingest", help="Ingest a source file")
    p_ingest.add_argument("file", help="Path to the source file (e.g. raw/article.md)")

    p_query = subparsers.add_parser("query", help="Ask a one-shot question")
    p_query.add_argument("question", help="The question to answer")

    subparsers.add_parser("lint", help="Run a wiki health check")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "chat": cmd_chat,
        "ingest": cmd_ingest,
        "query": cmd_query,
        "lint": cmd_lint,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()