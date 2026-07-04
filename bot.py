"""
Freelance AI Bot — ищет AI/ML задачи на фриланс-биржах.
Парсит Fl.ru, Freelancer.com.
Анализирует через AI, генерирует отклики.
Версия по примеру bankrot-bot (requests + Flask + webhook).
"""
import os, re, sys, time, json, logging, requests, threading
from datetime import datetime
from flask import Flask, request as flask_request
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# === Конфигурация (как в bankrot-bot) ===
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")
PARSING_INTERVAL = int(os.getenv("PARSING_INTERVAL_MINUTES", "15"))
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("freelance")

# === Telegram (точно как в bankrot-bot) ===

def api_call(method, **kwargs):
    try:
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=30)
        return r.json()
    except Exception as e:
        log.error(f"API {method}: {e}")
        return {"ok": False}

def send_message(cid, text, parse_mode="HTML"):
    if len(text) > 4000:
        parts, t = [], text
        while t:
            if len(t) <= 4000:
                parts.append(t); break
            cut = t[:4000].rfind("\n\n")
            if cut < 0: cut = t[:4000].rfind("\n")
            if cut < 0: cut = 4000
            parts.append(t[:cut]); t = t[cut:]
        for p in parts:
            api_call("sendMessage", chat_id=cid, text=p, parse_mode=parse_mode,
                     disable_web_page_preview=True)
    else:
        api_call("sendMessage", chat_id=cid, text=text, parse_mode=parse_mode,
                 disable_web_page_preview=True)

def answer(cid, text):
    send_message(cid, text)

# === Chat IDs (как в bankrot-bot) ===

def load_chat_ids():
    ids = set()
    if CHAT_ID: ids.add(int(CHAT_ID))
    try:
        with open("chat_ids.txt") as f:
            for line in f:
                l = line.strip()
                if l: ids.add(int(l))
    except FileNotFoundError: pass
    return ids

def save_chat_id(cid):
    ids = load_chat_ids()
    if cid not in ids:
        with open("chat_ids.txt", "a") as f:
            f.write(f"{cid}\n")

# === LLM (OpenRouter, как в bankrot-bot) ===

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def llm_call(system_prompt, user_msg, max_tokens=600, retry=0):
    if not OPENROUTER_API_KEY:
        return None
    models = ["deepseek/deepseek-v4-flash", "deepseek/deepseek-chat", "openai/gpt-4o-mini"]
    model = models[retry] if retry < len(models) else models[0]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://freelance-ai-bot.local",
        "X-Title": "Freelance AI Bot",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        elif r.status_code == 429:
            time.sleep(12)
            if retry < len(models) - 1:
                return llm_call(system_prompt, user_msg, max_tokens, retry + 1)
        else:
            log.error(f"OpenRouter {r.status_code}")
            if retry < len(models) - 1:
                return llm_call(system_prompt, user_msg, max_tokens, retry + 1)
    except Exception as e:
        log.error(f"LLM error: {e}")
    return None

# === AI-фильтр ===

AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "nlp", "natural language", "computer vision",
    "chatbot", "llm", "gpt", "openai", "pytorch", "tensorflow",
    "ai agent", "rag", "diffusion", "hugging face", "langchain",
    "нейросеть", "нейронная сеть", "машинное обучение",
    "нейросети", "deepfake", "yolo", "transformer", "bert",
    "ai ", " ai", " ii", "ии ", "deepseek", "gemini", "claude",
]

def is_ai_task(title, description=""):
    text = f"{title} {description}".lower()
    matches = sum(1 for kw in AI_KEYWORDS if kw in text)
    return matches >= 2

# === Хранилище задач ===

TASKS_FILE = "data/tasks.json"

def load_tasks():
    try:
        with open(TASKS_FILE) as f:
            return json.load(f)
    except:
        return []

def save_tasks(tasks):
    os.makedirs("data", exist_ok=True)
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def add_task(task):
    tasks = load_tasks()
    urls = {t["url"] for t in tasks}
    if task["url"] in urls:
        return False
    task["found_at"] = datetime.utcnow().isoformat()
    task["ai_score"] = None
    task["ai_analysis"] = None
    task["ai_response"] = None
    task["status"] = "new"
    tasks.append(task)
    save_tasks(tasks)
    return True

# === Парсинг Fl.ru ===

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

def parse_flru():
    projects = []
    urls = [
        "https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/",
        "https://www.fl.ru/projects/",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(script.string)
                        items = []
                        if isinstance(data, dict):
                            if data.get("@type") == "ItemList":
                                items = data.get("itemListElement", [])
                            else:
                                for g in data.get("@graph", []):
                                    if g.get("@type") == "ItemList":
                                        items = g.get("itemListElement", [])
                                        break
                        for item in items:
                            projects.append({
                                "platform": "flru",
                                "title": item.get("name", ""),
                                "url": item.get("url", ""),
                            })
                    except:
                        pass
            time.sleep(1)
        except Exception as e:
            log.error(f"Fl.ru: {e}")
    return projects

# === Парсинг Freelancer.com ===

def parse_freelancer():
    projects = []
    api_url = "https://www.freelancer.com/api/projects/0.1/projects/active"
    for kw in ["AI", "machine learning", "Python", "neural network", "chatbot"]:
        try:
            params = {
                "query": kw, "limit": 20, "offset": 0,
                "job_details": "true", "user_details": "true",
                "status": "open", "sort_field": "submitdate",
            }
            r = requests.get(api_url, params=params, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.freelancer.com/",
            }, timeout=20)
            if r.status_code == 200:
                data = r.json()
                for proj in data.get("result", {}).get("projects", []):
                    pid = proj.get("seo_url") or proj.get("id", "")
                    projects.append({
                        "platform": "freelancer",
                        "title": proj.get("title", ""),
                        "url": f"https://www.freelancer.com/projects/{pid}" if pid else "",
                        "description": (proj.get("description", "") or "")[:1000],
                        "skills": [j.get("name", "") for j in proj.get("jobs", [])],
                    })
            time.sleep(1)
        except Exception as e:
            log.error(f"Freelancer: {e}")
    return projects

# === Парсинг GitHub (Issues с тегами job/hiring) ===

GITHUB_REPOS = [
    "remote-jobs/remote-jobs",
    "oss-listings/oss-listings",
    "pytorch/pytorch",
    "huggingface/transformers",
    "langchain-ai/langchain",
    "openai/openai-python",
    "ollama/ollama",
    "vllm-project/vllm",
    "AUTOMATIC1111/stable-diffusion-webui",
    "comfyanonymous/ComfyUI",
    "ggml-org/llama.cpp",
    "microsoft/DeepSpeed",
    "mlflow/mlflow",
    "ray-project/ray",
    "airbytehq/airbyte",
    "run-llama/llama_index",
    "chatchat-space/Langchain-Chatchat",
    "infiniflow/ragflow",
    "binary-husky/gpt_academic",
]

def parse_github():
    """Ищет Issues с тегами job/hiring/bounty в AI/ML репозиториях."""
    projects = []
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "FreelanceAI-Bot",
    }
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {os.getenv('GITHUB_TOKEN')}"

    for repo in GITHUB_REPOS:
        try:
            # Ищем Issues с лейблами job, hiring, bounty, freelance
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {"state": "open", "per_page": 30, "sort": "created", "direction": "desc"}
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                for issue in r.json():
                    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]
                    title = issue.get("title", "")
                    body = (issue.get("body", "") or "")[:1500]
                    text = f"{title} {body}".lower()

                    # Проверяем теги или ключевые слова в заголовке
                    is_job = (
                        any(l in labels for l in ["job", "jobs", "hiring", "bounty", "freelance", "work", "position"])
                        or any(kw in title.lower() for kw in ["hiring", "job", "freelance", "bounty", "position", "we are looking"])
                    )
                    if is_job:
                        projects.append({
                            "platform": "github",
                            "title": title,
                            "url": issue.get("html_url", ""),
                            "description": body[:1000],
                            "skills": labels[:10],
                        })
            time.sleep(0.5)  # GitHub rate limit
        except Exception as e:
            log.error(f"GitHub {repo}: {e}")

    return projects

# === Парсинг всех платформ ===

def parse_all():
    all_projects = []
    all_projects.extend(parse_flru())
    all_projects.extend(parse_freelancer())
    all_projects.extend(parse_github())

    new_count = 0
    ai_tasks = []
    for proj in all_projects:
        if proj.get("title") and proj.get("url"):
            if is_ai_task(proj.get("title", ""), proj.get("description", "")):
                if add_task(proj):
                    new_count += 1
                    ai_tasks.append(proj)
    log.info(f"Парсинг: {len(all_projects)} всего, {new_count} новых AI-задач")
    return ai_tasks

# === Форматирование ===

def fmt_task(task, i):
    platform = task.get("platform", "?")
    title = task.get("title", "?")[:80]
    url = task.get("url", "")
    score = task.get("ai_score")
    score_str = f"\nAI Score: {score}/100" if score else ""
    return (
        f"<b>{i}. {title}</b>\n"
        f"Платформа: {platform}{score_str}\n"
        f"<a href=\"{url}\">Открыть задачу</a>"
    )

# === Команды (как в bankrot-bot) ===

_last_tasks = []

def handle_command(cid, text, user):
    global _last_tasks
    cmd = text.split()[0].lower() if text else ""
    args = text.split()[1:] if text else []
    log.info(f"Command {cmd} from chat={cid}")

    if cmd == "/start":
        answer(cid,
            "<b>Freelance AI Bot</b>\n\n"
            "Ищу AI/ML задачи на фриланс-биржах.\n"
            "Анализирую через нейросеть.\n"
            "Генерирую отклики.\n\n"
            "<b>Биржи:</b> Fl.ru, Freelancer.com\n\n"
            "<b>Команды:</b>\n"
            "/parse — поиск задач\n"
            "/tasks — показать задачи\n"
            "/analyze 3 — AI-анализ задачи #3\n"
            "/respond 3 — сгенерировать отклик\n"
            "/stats — статистика\n"
            "/help — справка")

    elif cmd == "/help":
        llm_status = "OK" if OPENROUTER_API_KEY else "NOT SET"
        answer(cid,
            "<b>Как пользоваться:</b>\n\n"
            "/parse — запустить парсинг\n"
            "/tasks — показать задачи\n"
            "/analyze 3 — AI-анализ\n"
            "/respond 3 — отклик\n"
            "/stats — статистика\n\n"
            f"LLM: {llm_status}")

    elif cmd == "/parse":
        answer(cid, "Парсинг Fl.ru + Freelancer.com + GitHub...")
        try:
            ai_tasks = parse_all()
            _last_tasks = ai_tasks
            if ai_tasks:
                msg = f"<b>Найдено {len(ai_tasks)} AI-задач:</b>\n\n"
                for i, task in enumerate(ai_tasks[:10], 1):
                    msg += fmt_task(task, i) + "\n\n"
                answer(cid, msg)
            else:
                answer(cid, "AI-задач не найдено.")
        except Exception as e:
            log.error(f"Parse error: {e}", exc_info=True)
            answer(cid, f"Ошибка: {e}")

    elif cmd == "/tasks":
        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.get("status") == "new"]
        if not new_tasks:
            answer(cid, "Нет задач. /parse")
            return
        msg = f"<b>Задач: {len(new_tasks)}</b>\n\n"
        for i, t in enumerate(new_tasks[:20], 1):
            msg += fmt_task(t, i) + "\n\n"
        answer(cid, msg)

    elif cmd == "/analyze":
        if not args:
            answer(cid, "Использование: /analyze 3")
            return
        try:
            num = int(args[0])
        except:
            answer(cid, "Номер должен быть числом.")
            return
        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.get("status") == "new"]
        if num < 1 or num > len(new_tasks):
            answer(cid, f"Задача #{num} не найдена. Доступно: 1-{len(new_tasks)}")
            return
        task = new_tasks[num - 1]
        answer(cid, f"AI-анализ: {task['title'][:50]}...")

        system = """Ты ищешь работу AI/ML разработчика.
Оцени задачу 0-100 (насколько подходит AI/ML разработчику).
Ответь ТОЛЬКО JSON: {"score": 0-100, "analysis": "2-3 предложения", "reply": "да/нет/может"}
Без markdown."""
        user = f"Задача: {task['title']}\nОписание: {task.get('description', '')[:1500]}"
        result = llm_call(system, user, max_tokens=200)
        if result:
            try:
                clean = re.sub(r"```json\n?|\n?```", "", result).strip()
                data = json.loads(clean)
                tasks = load_tasks()
                for t in tasks:
                    if t["url"] == task["url"]:
                        t["ai_score"] = data.get("score")
                        t["ai_analysis"] = data.get("analysis")
                        t["status"] = "analyzed"
                        break
                save_tasks(tasks)
                msg = (
                    f"<b>AI-анализ:</b>\n\n"
                    f"<b>{task['title'][:80]}</b>\n\n"
                    f"Score: <b>{data.get('score', '?')}/100</b>\n"
                    f"{data.get('analysis', '')}\n"
                    f"Рекомендация: {data.get('reply', '?')}\n\n"
                    f"<a href=\"{task['url']}\">Открыть</a>"
                )
                answer(cid, msg)
            except:
                answer(cid, f"Ошибка парсинга ответа: {result[:200]}")
        else:
            answer(cid, "LLM недоступен.")

    elif cmd == "/respond":
        if not args:
            answer(cid, "Использование: /respond 3")
            return
        try:
            num = int(args[0])
        except:
            answer(cid, "Номер должен быть числом.")
            return
        tasks = load_tasks()
        new_tasks = [t for t in tasks if t.get("status") == "new"]
        if num < 1 or num > len(new_tasks):
            answer(cid, f"Задача #{num} не найдена. Доступно: 1-{len(new_tasks)}")
            return
        task = new_tasks[num - 1]
        answer(cid, "Генерирую отклик... 30 сек.")

        system = """Ты — AI/ML разработчик. Напиши отклик на задачу (150-250 слов).
Язык = язык задачи. Будь конкретным, упомяни технологии, предложи план.
Не ставь цену — скажи "готов обсудить бюджет"."""
        user = f"Задача: {task['title']}\nОписание: {task.get('description', '')[:2000]}"
        response = llm_call(system, user, max_tokens=800)
        if response:
            tasks = load_tasks()
            for t in tasks:
                if t["url"] == task["url"]:
                    t["ai_response"] = response
                    t["status"] = "responded"
                    break
            save_tasks(tasks)
            msg = (
                f"<b>Отклик:</b>\n\n"
                f"<b>{task['title'][:80]}</b>\n\n"
                f"{response}\n\n"
                f"<a href=\"{task['url']}\">Открыть задачу</a>"
            )
            answer(cid, msg)
        else:
            answer(cid, "LLM недоступен.")

    elif cmd == "/stats":
        tasks = load_tasks()
        by_p = {}
        for t in tasks:
            p = t.get("platform", "?")
            by_p[p] = by_p.get(p, 0) + 1
        lines = [f"<b>Статистика:</b>\nВсего: {len(tasks)}"]
        for p, c in sorted(by_p.items()):
            lines.append(f"  {p}: {c}")
        answer(cid, "\n".join(lines))

    else:
        answer(cid, "Неизвестная команда. /help")

# === Автопарсинг ===

def scheduler():
    while True:
        try:
            log.info("Автопарсинг...")
            ai_tasks = parse_all()
            if ai_tasks:
                for cid in load_chat_ids():
                    msg = f"<b>Новые AI-задачи! ({len(ai_tasks)})</b>\n\n"
                    for i, t in enumerate(ai_tasks[:5], 1):
                        msg += fmt_task(t, i) + "\n\n"
                    send_message(cid, msg)
        except Exception as e:
            log.error(f"Scheduler: {e}")
        time.sleep(PARSING_INTERVAL * 60)

# === Flask (как в bankrot-bot) ===

app = Flask(__name__)

@app.route("/")
def index():
    return "Freelance AI Bot is running!", 200

@app.route("/health")
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = flask_request.get_json()
        if not data: return "OK", 200
        msg = data.get("message")
        if not msg: return "OK", 200
        save_chat_id(msg["chat"]["id"])
        text = msg.get("text", "")
        if text.startswith("/"):
            threading.Thread(target=handle_command,
                           args=(msg["chat"]["id"], text, msg.get("from", {})),
                           daemon=True).start()
    except Exception as e:
        log.error(f"Webhook: {e}", exc_info=True)
    return "OK", 200

# === Main (как в bankrot-bot) ===

def main():
    log.info("Starting Freelance AI Bot...")
    if not BOT_TOKEN:
        log.error("TELEGRAM_TOKEN not set!"); return

    if RENDER_EXTERNAL_URL:
        r = api_call("setWebhook", url=f"{RENDER_EXTERNAL_URL}/webhook")
        log.info(f"Webhook: {r}")

    api_call("setMyCommands", commands=[
        {"command": "parse", "description": "Поиск AI-задач"},
        {"command": "tasks", "description": "Показать задачи"},
        {"command": "analyze", "description": "AI-анализ задачи"},
        {"command": "respond", "description": "Сгенерировать отклик"},
        {"command": "stats", "description": "Статистика"},
        {"command": "help", "description": "Справка"},
    ])

    threading.Thread(target=scheduler, daemon=True).start()
    log.info(f"Автопарсинг каждые {PARSING_INTERVAL} мин")

    log.info(f"Server on {PORT}...")
    app.run(host="0.0.0.0", port=PORT, debug=False)

if __name__ == "__main__":
    main()
