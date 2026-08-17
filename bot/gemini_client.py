"""
gemini_client.py — Wraps Gemini API calls for the resume tailoring bot.

Uses the newer google-genai SDK (v1.5+) which supports Python 3.14.
"""

import os
import re
import json
import time
import logging
import json_repair
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from tenacity import AsyncRetrying, wait_random_exponential, stop_after_attempt, retry_if_exception
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError
from models import ResumeEvaluation, ResumeRevisions, CompanySnippet, TailoredResumeContent
from html_builder import build_resume_html, build_education_html
from prompts import build_evaluator_prompt, build_generator_prompt
import analytics_logger

logger = logging.getLogger(__name__)

# ─── Rate-limit helpers ────────────────────────────────────────────────────────

def _is_rate_limit(e: BaseException) -> bool:
    code = getattr(e, 'code', None) or getattr(e, 'status_code', None)
    if code == 429:
        return True
    msg = str(e).upper()
    return 'RESOURCE_EXHAUSTED' in msg or "'CODE': 429" in msg or '"CODE": 429' in msg


def _get_retry_after(e: BaseException) -> float | None:
    """Extract seconds-to-wait from a Gemini 429 error.

    Gemini returns the delay as google.rpc.RetryInfo inside the error body:
      'details': [{'@type': '...RetryInfo', 'retryDelay': '43s'}]
    It also prints it in the human-readable message: "Please retry in 43.10s."
    Standard HTTP Retry-After headers are also checked as a fallback.
    """
    msg = str(e)

    # 1. google.rpc.RetryInfo — "retryDelay": "43s" / 'retryDelay': '43s'
    m = re.search(r"""['"]retryDelay['"]\s*:\s*['"](\d+(?:\.\d+)?)s['"]""", msg, re.IGNORECASE)
    if m:
        return max(1.0, float(m.group(1)))

    # 2. Human-readable message: "Please retry in 43.10s" or "retry in 43s"
    m = re.search(r'retry(?:ing)? in (\d+(?:\.\d+)?)\s*s', msg, re.IGNORECASE)
    if m:
        return max(1.0, float(m.group(1)))

    # 3. Standard HTTP Retry-After header via httpx response object
    for attr in ('response', '_response'):
        resp = getattr(e, attr, None)
        if resp is None:
            continue
        headers = getattr(resp, 'headers', {}) or {}
        for h in ('retry-after', 'Retry-After', 'x-ratelimit-reset'):
            val = headers.get(h)
            if val:
                try:
                    return max(1.0, float(val))
                except (ValueError, TypeError):
                    pass
    return None


# ─── API key pool ──────────────────────────────────────────────────────────────

class GeminiKeyPool:
    """Manages a pool of Gemini API keys with per-key rate-limit cooldowns."""

    # If a retry-after exceeds this we rotate instead of waiting
    _WAIT_THRESHOLD = 120.0
    # Default cooldown when no retry-after is available
    _DEFAULT_COOLDOWN = 65.0

    def __init__(self):
        self._keys = self._load_keys()
        if not self._keys:
            raise RuntimeError('GEMINI_API_KEY environment variable not set.')
        self._clients: list[genai.Client | None] = [None] * len(self._keys)
        self._current_idx = 0
        self._available_at: list[float] = [0.0] * len(self._keys)
        if len(self._keys) > 1:
            logger.info(f"GeminiKeyPool: {len(self._keys)} API keys loaded.")

    def _load_keys(self) -> list[str]:
        seen: set[str] = set()
        keys: list[str] = []
        for k in os.environ.get('GEMINI_API_KEYS', '').split(','):
            k = k.strip()
            if k and k not in seen:
                seen.add(k); keys.append(k)
        for suffix in ['', '_2', '_3', '_4', '_5']:
            k = os.environ.get(f'GEMINI_API_KEY{suffix}', '').strip()
            if k and k not in seen:
                seen.add(k); keys.append(k)
        return keys

    def _build_client(self, idx: int) -> genai.Client:
        if self._clients[idx] is None:
            self._clients[idx] = genai.Client(
                api_key=self._keys[idx],
                http_options={'timeout': 600000},
            )
        return self._clients[idx]

    @property
    def client(self) -> genai.Client:
        return self._build_client(self._current_idx)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def rotate(self, retry_after: float | None):
        """Mark current key as rate-limited and move to the next available one."""
        cooldown = retry_after if retry_after is not None else self._DEFAULT_COOLDOWN
        self._available_at[self._current_idx] = time.monotonic() + cooldown
        logger.warning(
            f"Key pool: key #{self._current_idx + 1}/{self.key_count} "
            f"rate-limited for {cooldown:.1f}s."
        )
        now = time.monotonic()
        for offset in range(1, self.key_count):
            candidate = (self._current_idx + offset) % self.key_count
            if self._available_at[candidate] <= now:
                logger.info(f"Key pool: switched to key #{candidate + 1}.")
                self._current_idx = candidate
                return
        logger.warning("Key pool: all keys are rate-limited — will wait for the earliest one.")

    async def wait_for_available(self):
        """Sleep until the key with the soonest cooldown expiry is ready."""
        now = time.monotonic()
        waits = [max(0.0, self._available_at[i] - now) for i in range(self.key_count)]
        best = min(range(self.key_count), key=lambda i: waits[i])
        wait_secs = waits[best]
        if wait_secs > 0:
            logger.info(
                f"Key pool: waiting {wait_secs:.1f}s for key #{best + 1} to become available..."
            )
            await asyncio.sleep(wait_secs + 0.5)
        self._current_idx = best


# ─── Skill file paths ─────────────────────────────────────────────────────────
_BOT_DIR = os.path.dirname(__file__)
_REPO_ROOT = os.path.join(_BOT_DIR, '..')

# Try the plugin location first (local dev), then repo root (Render)
SKILL_FILE = os.path.join(
    os.path.expandvars(r'%USERPROFILE%'),
    '.gemini', 'config', 'plugins', 'my-custom-skills',
    'skills', 'tailor_resume_skill.md'
)
MASTER_PROFILE_FILE = os.path.join(_REPO_ROOT, 'master_profile.json')

# Fallback: bundled copies in repo root (for Render)
_ALT_SKILL = os.path.join(_REPO_ROOT, 'tailor_resume_skill.md')




def _read_file(primary, fallback=''):
    for p in [primary, fallback]:
        if p and os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return f.read()
    return ''


def _load_base_resume(role_hint: str, base_dir: str):
    """Pick fullstack or backend resume based on role hint."""
    fullstack = os.path.join(base_dir, 'PurveshGandhi_Base_Resume_1_Fullstack.html')
    backend = os.path.join(base_dir, 'PurveshGandhi_Base_Resume_2_Backend.html')

    backend_kw = ['backend', 'back-end', 'back end', 'api', 'microservice',
                  'distributed', 'golang', 'rust', 'java developer', 'python developer']
    use_backend = any(kw in role_hint.lower() for kw in backend_kw)

    chosen = backend if use_backend else fullstack
    if not os.path.exists(chosen):
        chosen = fullstack  # fallback

    if os.path.exists(chosen):
        with open(chosen, 'r', encoding='utf-8') as f:
            return f.read(), os.path.basename(chosen)
    return '', 'unknown'

def _truncate_jd_boilerplate(jd: str) -> str:
    """Removes standard Indian market boilerplate and EEO statements to save context window."""
    boilerplate_markers = [
        "Equal Opportunity Employer",
        "is an equal opportunity employer",
        "does not charge any fee at any stage",
        "fraudulent job offers",
        "Rights of Persons with Disabilities",
        "caste, religion, gender, sexual orientation"
    ]
    jd_lower = jd.lower()
    earliest_idx = len(jd)
    start_search_idx = len(jd) // 2
    for marker in boilerplate_markers:
        idx = jd_lower.find(marker.lower(), start_search_idx)
        if idx != -1 and idx < earliest_idx:
            earliest_idx = idx
    if earliest_idx < len(jd):
        return jd[:earliest_idx].strip()
    return jd


class GeminiClient:
    def __init__(self, session_id=None):
        self.session_id = session_id
        self._pool = GeminiKeyPool()

    @property
    def client(self) -> genai.Client:
        """Always returns the currently active key's client — updates after key rotation."""
        return self._pool.client

    async def _execute_with_ratelimit(self, call_fn, allow_key_rotation: bool = True):
        """Run call_fn(), handling 429 rate limits before they reach tenacity.

        - 429 with retry-after <= 120s  → wait the specified time, retry same key.
        - 429 with no/long retry-after  → rotate to next key (or wait if all exhausted).
        - Non-429 errors                → re-raised immediately so tenacity can retry them.
        """
        max_attempts = self._pool.key_count * 3 + 1
        last_exc: BaseException | None = None
        for _ in range(max_attempts):
            try:
                return await call_fn()
            except (APIError, ClientError) as e:
                if not _is_rate_limit(e):
                    raise  # bubble up to tenacity
                last_exc = e
                retry_after = _get_retry_after(e)
                if allow_key_rotation and self._pool.key_count > 1:
                    # With multiple keys, always try rotating first — waiting on
                    # the same key is pointless when another key may be available.
                    self._pool.rotate(retry_after)
                    await self._pool.wait_for_available()
                elif retry_after is not None and retry_after <= GeminiKeyPool._WAIT_THRESHOLD:
                    logger.info(
                        f"Rate limited (key #{self._pool._current_idx + 1}): "
                        f"retry-after={retry_after:.1f}s — waiting..."
                    )
                    await asyncio.sleep(retry_after + 0.5)
                elif allow_key_rotation:
                    self._pool.rotate(retry_after)
                    await self._pool.wait_for_available()
                else:
                    # Chat sessions can't rotate keys mid-session — just wait
                    wait = retry_after if retry_after is not None else GeminiKeyPool._DEFAULT_COOLDOWN
                    logger.info(f"Rate limited (chat, no rotation): waiting {wait:.1f}s...")
                    await asyncio.sleep(wait + 0.5)
        raise last_exc or RuntimeError("Rate limit: all attempts exhausted.")

    # Standard paid-tier pricing per 1M tokens (June 2026).
    # Source: https://ai.google.dev/gemini-api/docs/pricing
    # thoughts_token_count is billed at the output rate and folded into completion_tokens.
    # cached_content_token_count is a subset of prompt_token_count charged at the cache rate.
    _MODEL_PRICING: dict[str, dict[str, float]] = {
        'gemini-3.5-flash':      {'input': 1.50, 'output': 9.00,  'cached': 0.15},
        'gemini-3.1-pro-preview': {'input': 2.00, 'output': 12.00, 'cached': 0.20},  # <=200k token tier; >200k: input $4, output $18, cached $0.40
        'gemini-3-flash-preview': {'input': 0.50, 'output': 3.00,  'cached': 0.05},
        'gemini-2.5-flash':      {'input': 0.30, 'output': 2.50,  'cached': 0.03},
        'gemini-3.1-flash-lite': {'input': 0.25, 'output': 1.50,  'cached': 0.025},
    }

    @staticmethod
    def _make_thinking_config(model: str, level: str) -> types.ThinkingConfig:
        """Returns the correct ThinkingConfig for a model family.
        Gemini 2.5.x uses thinking_budget (int); Gemini 3.x uses thinking_level (str).
        """
        _level_to_budget = {'none': 0, 'low': 1024, 'medium': 4096, 'high': 16384}
        if model.startswith('gemini-2.'):
            return types.ThinkingConfig(thinking_budget=_level_to_budget.get(level, 1024))
        return types.ThinkingConfig(thinking_level=level)

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, model: str = 'gemini-3.5-flash') -> float:
        pricing = self._MODEL_PRICING.get(model, self._MODEL_PRICING['gemini-3.5-flash'])
        non_cached_input = max(0, prompt_tokens - cached_tokens)
        return (
            (non_cached_input / 1_000_000 * pricing['input'])
            + (cached_tokens  / 1_000_000 * pricing['cached'])
            + (completion_tokens / 1_000_000 * pricing['output'])
        )



    async def start_chat_session(self, jd: str, base_resumes_dir: str, status_callback=None) -> dict:
        """
        Starts a chat session with the JD as the first message.
        Returns dict containing the chat object, first response text, and base resume name.
        """
        boundary = _read_file(MASTER_PROFILE_FILE)
        skill_instructions = _read_file(SKILL_FILE, _ALT_SKILL)
        base_html, base_name = _load_base_resume(jd, base_resumes_dir)

        if not base_html:
            raise RuntimeError('Could not load any base resume HTML file.')

        # Strip terminal commands from the prompt so Gemini doesn't hallucinate function calls
        sanitized_skill = re.sub(r'### Step 5: PDF Generation.*?### Step 6:', '### Step 6:', skill_instructions, flags=re.DOTALL)
        sanitized_skill = re.sub(r'### Step 7: Google Drive Upload.*', '', sanitized_skill, flags=re.DOTALL)

        system_prompt = f"""{sanitized_skill}

---

## OVERRIDE FOR API EXECUTION
You are running as a headless text-generation API inside a Python wrapper.
DO NOT output any function calls. DO NOT attempt to run any terminal commands or upload anything to Google Drive.
DO NOT generate the Company Guide or the Summary (these are handled by a separate background research agent).
Your ONLY job is to expertly rewrite the HTML and return JSON with `company_name` and `tailored_html`.

---

## MASTER PROFILE (CORE SKILLS & EXPERIENCE)
{boundary}

---

## CANDIDATE'S BASE RESUME (HTML)
You must modify this HTML for the tailored output. Do NOT edit the base file.
```html
{base_html}
```

---

## OUTPUT FORMAT
If you need clarification about adding a high-signal skill, ASK THE USER FIRST.
Do not generate the resume until your questions are answered.
When ready, return a JSON object with:
- `company_name`: the company name with no spaces (e.g. "AlignTechnology")
- `tailored_html`: the complete tailored resume as a full HTML document
"""

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            max_output_tokens=32768,  # JSON-encoded HTML is ~1.2 tokens/char — needs more room than plain text
            thinking_config=types.ThinkingConfig(thinking_level="high"),
            response_mime_type="application/json",
            response_schema=TailoredResumeContent,  # legacy path — tailor_process() uses refine_resume() instead
        )

        chat = None
        
        # Determine primary model based on priority
        model_name = 'gemini-3.5-flash'
        logger.info(f'Starting Gemini chat session ({model_name})...')
        chat = self.client.aio.chats.create(model=model_name, config=config)

        res_data = await self.send_message_with_retry(chat, f"Here is the Job Description:\n\n{jd}", status_callback)
        
        # Log audit details
        if res_data['error']:
            if self.session_id:
                await analytics_logger.log_llm_request(self.session_id, 'start_chat_session', chat._model, 0, 0, 0.0, error=res_data['error'])
        else:
            cost = self._calculate_cost(res_data['prompt_tokens'], res_data['completion_tokens'], model=chat._model)
            if self.session_id:
                await analytics_logger.log_llm_request(self.session_id, 'start_chat_session', chat._model, res_data['prompt_tokens'], res_data['completion_tokens'], cost, cached_tokens=res_data.get('cached_tokens', 0))
            res_data['cost'] = cost
            res_data['model_used'] = chat._model

        return {
            'chat': chat,
            'response_text': res_data['text'],
            'base_resume_used': base_name,
            'usage': res_data
        }

    async def classify_company(self, jd: str) -> dict:
        """
        Lightweight company classifier. Runs fast with Google Search grounding
        and returns only what the resume generator actually needs:
          - company_type  (product_startup | it_services | gcc)
          - target_title, must_have_keywords, nice_to_have_keywords
        Uses Flash model + grounding for speed and cost efficiency.
        """
        jd = _truncate_jd_boilerplate(jd)

        system_prompt = """You are a senior technical recruiter with deep knowledge of the Indian tech market.

Your task: given a job description, extract:
1. The company type — one of: "product_startup", "it_services", or "gcc"
   - product_startup: tech-first product companies, startups, scale-ups, FAANG-adjacent
   - gcc: Global Capability Centers / captive centres for MNCs (e.g. Walmart Global Tech, Target, JPMorgan)
   - it_services: outsourcing/services firms (TCS, Infosys, Wipro, Capgemini, etc.)
2. The exact target role title from the JD (verbatim, e.g. "Senior Backend Engineer"). Empty string if none stated.
3. 5–12 must-have hard skills, tools, and technologies named or strongly implied as required in the JD. Use exact surface forms as they appear in the JD (e.g. "PostgreSQL", not "SQL database").
4. 0–8 secondary/preferred keywords (nice-to-have, mentioned as a plus or preferred).

Items 2–4 come directly from reading the JD text. For item 1, use your training knowledge to classify the company.
Be concise.
Output strict JSON only."""

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.3,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            response_mime_type="application/json",
            response_schema=CompanySnippet,
        )

        model_name = 'gemini-3.1-flash-lite'
        logger.info(f'Starting company classification ({model_name})...')

        retryer = AsyncRetrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(4),
            retry=retry_if_exception(lambda e: isinstance(e, (APIError, ClientError)) and not _is_rate_limit(e)),
            reraise=True
        )

        response = None
        async for attempt in retryer:
            with attempt:
                response = await self._execute_with_ratelimit(
                    lambda: self.client.aio.models.generate_content(
                        model=model_name,
                        contents=f"Job Description:\n\n{jd}",
                        config=config
                    )
                )

        response_text = response.text if response else ''
        company_type = 'product_startup'
        target_title = ''
        must_have_keywords: list[str] = []
        nice_to_have_keywords: list[str] = []

        if response_text:
            try:
                data = json_repair.loads(response_text)
                if isinstance(data, str):
                    data = json.loads(data)
                snippet = CompanySnippet(**data)
                raw_type = snippet.company_type.lower()
                if 'services' in raw_type or 'agency' in raw_type:
                    company_type = 'it_services'
                elif any(k in raw_type for k in ('gcc', 'capability', 'captive', 'global tech', 'gbs')):
                    company_type = 'gcc'
                else:
                    company_type = 'product_startup'
                target_title = snippet.target_title
                must_have_keywords = snippet.must_have_keywords
                nice_to_have_keywords = snippet.nice_to_have_keywords
            except Exception as e:
                logger.error(f'Failed to parse company classification JSON: {e}')

        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        if response and getattr(response, 'usage_metadata', None):
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0
            completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) or 0) + thinking_tokens
            cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0

        cost = self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=model_name)
        if self.session_id:
            await analytics_logger.log_llm_request(self.session_id, 'classify_company', model_name, prompt_tokens, completion_tokens, cost, cached_tokens=cached_tokens)

        return {
            'company_type': company_type,
            'target_title': target_title,
            'must_have_keywords': must_have_keywords,
            'nice_to_have_keywords': nice_to_have_keywords,
            'usage': {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'cost': cost,
                'model_used': model_name
            }
        }



    async def revise_resume(self, current_html: str, company_name: str, jd: str, feedback: str) -> dict:
        """
        Lean stateless revision call. Uses only the current tailored HTML + feedback
        instead of re-sending the full skill file, base resume, and chat history.
        Dramatically cheaper than continuing the original chat session.
        Returns targeted JSON edits to save on output tokens.
        """


        revision_system_prompt = f"""You are an expert resume editor. You will receive a fully tailored HTML resume and a specific revision request. Apply the changes precisely by returning a list of targeted text replacements.

## EDITING RULES (strictly enforced)
- Address the feedback AND NOTHING ELSE. Do not rewrite the entire file.
- Provide the exact contiguous text block in the current HTML to be replaced. Ensure the `search_string` is unique and exactly matches the existing HTML including whitespace.
- Provide the `replacement_string` with the new text/HTML.
- Do NOT use Markdown formatting inside HTML. Use only proper HTML tags: <strong>, <em>, <b>.
- Do NOT alter the CSS, layout, margins, or fonts. Only edit text content and bullet points.
- Every bullet must describe a consequence — what got faster, more reliable, or more scalable — not just a task.
- Every bullet must contain at least one of: a specific technology name, a metric, an architectural pattern, or a problem name.
- Start each bullet with a strong action verb: Engineered, Architected, Optimized, Eliminated, Automated, Refactored, Decoupled, Instrumented, Migrated, Hardened, Scaled.
- Do NOT fabricate new metrics. Reuse or reframe existing quantifiable metrics only.
- The resume must remain within one page — do not add so many bullets that it overflows.

## JD CONTEXT (for keyword alignment during edits)
{jd}
"""

        user_message = (
            f"Current tailored HTML resume:\n```html\n{current_html}\n```\n\n"
            f"Revision request: {feedback}"
        )

        model_name = 'gemini-2.5-flash'
        config = types.GenerateContentConfig(
            system_instruction=revision_system_prompt,
            temperature=0.2,
            max_output_tokens=8192,  # HTML search/replacement strings can be long
            thinking_config=GeminiClient._make_thinking_config(model_name, 'low'),
            response_mime_type="application/json",
            response_schema=ResumeRevisions,
        )

        logger.info(f'Starting stateless revision with targeted edits ({model_name})...')

        retryer = AsyncRetrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception(lambda e: isinstance(e, (APIError, ClientError)) and not _is_rate_limit(e)),
            reraise=True
        )

        response = None
        async for attempt in retryer:
            with attempt:
                if attempt.retry_state.attempt_number == 5:
                    model_name = 'gemini-3.5-flash'
                    logger.info(f"Revision: Switching to fallback model {model_name}...")
                    config = types.GenerateContentConfig(
                        system_instruction=revision_system_prompt,
                        temperature=0.2,
                        max_output_tokens=8192,
                        thinking_config=GeminiClient._make_thinking_config(model_name, 'low'),
                        response_mime_type="application/json",
                        response_schema=ResumeRevisions,
                    )
                response = await self._execute_with_ratelimit(
                    lambda: self.client.aio.models.generate_content(
                        model=model_name,
                        contents=user_message,
                        config=config
                    )
                )

        response_text = response.text if response else None

        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        if response and getattr(response, 'usage_metadata', None):
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0
            completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) or 0) + thinking_tokens
            cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0

        error_msg = None
        new_html = current_html
        
        if response_text is None:
            error_msg = "Gemini returned an empty or blocked response."
            fake_raw_response = "⚠️ Gemini returned an empty or blocked response."
        else:
            try:
                data = json.loads(response_text)
                revisions = ResumeRevisions(**data)
                
                # Apply patches iteratively
                for edit in revisions.edits:
                    if edit.search_string in new_html:
                        new_html = new_html.replace(edit.search_string, edit.replacement_string, 1)
                    else:
                        logger.warning(f"Revision edit missed: Could not find exact string:\\n{edit.search_string}")
                
                # Construct fake raw response for bot.py compatibility
                fake_raw_response = f"===COMPANY_NAME===\n{company_name}\n\n===TAILORED_HTML===\n{new_html}"
            except Exception as e:
                logger.error(f"Failed to parse or apply targeted edits: {e}")
                error_msg = f"Failed to parse or apply targeted edits: {e}"
                fake_raw_response = f"⚠️ Gemini returned invalid JSON or edits could not be applied: {e}"

        cost = self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=model_name)
        if self.session_id:
            await analytics_logger.log_llm_request(self.session_id, 'revise_resume', model_name, prompt_tokens, completion_tokens, cost, cached_tokens=cached_tokens, error=error_msg)

        return {
            'text': fake_raw_response,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'cost': cost,
            'model_used': model_name,
            'error': error_msg,
        }

    async def refine_resume(self, jd: str, company_type: str, master_profile_json: str, template_html: str, education_html: str = '', current_content: str = None, feedback: str = None, jd_keywords: str = '') -> dict:
        """Stateless generator that creates or refines a resume draft.

        Accepts structured content (TailoredResumeContent JSON) instead of raw HTML.
        Returns structured data including the assembled tailored_html and the raw
        current_content_json for the next iteration / evaluator call.
        """
        jd = _truncate_jd_boilerplate(jd)
        system_prompt, contents = build_generator_prompt(company_type, master_profile_json, jd, current_content, feedback, jd_keywords=jd_keywords)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            max_output_tokens=8192,  # resume JSON with full bullets can exceed 4096 — raised after truncation failures
            thinking_config=types.ThinkingConfig(thinking_level="high"),
            response_mime_type="application/json",
            response_schema=TailoredResumeContent,
        )

        model_name = 'gemini-3.5-flash'
        logger.info(f"Starting Generator Agent ({model_name}) for company_type: {company_type}...")

        retryer = AsyncRetrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception(lambda e: isinstance(e, (APIError, ClientError)) and not _is_rate_limit(e)),
            reraise=True
        )

        response = None
        async for attempt in retryer:
            with attempt:
                if attempt.retry_state.attempt_number == 5:
                    logger.info("Generator: Switching to fallback model gemini-2.5-flash...")
                    model_name = 'gemini-2.5-flash'
                    config = types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.7,
                        max_output_tokens=8192,
                        thinking_config=GeminiClient._make_thinking_config(model_name, 'high'),
                        response_mime_type="application/json",
                        response_schema=TailoredResumeContent,
                    )
                response = await self._execute_with_ratelimit(
                    lambda: self.client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config
                    )
                )

        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        if response and getattr(response, 'usage_metadata', None):
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0
            completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) or 0) + thinking_tokens
            cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0

        error_msg = None
        company_name = None
        tailored_html = None
        current_content_json = None

        if not response or not response.text:
            error_msg = "Gemini returned an empty or blocked response."
        else:
            try:
                content_obj = TailoredResumeContent.model_validate_json(response.text)
                company_name = content_obj.company_name
                current_content_json = response.text  # raw JSON string for next iteration / evaluator
                tailored_html = build_resume_html(template_html, content_obj, education_html)
            except Exception as e:
                error_msg = f"Failed to parse generator JSON: {e}"
                logger.error(f"{error_msg}\nRaw: {response.text[:500]}")

        cost = self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=model_name)
        if self.session_id:
            await analytics_logger.log_llm_request(self.session_id, 'tailor_generator', model_name, prompt_tokens, completion_tokens, cost, cached_tokens=cached_tokens, error=error_msg)

        return {
            'company_name': company_name,
            'tailored_html': tailored_html,
            'current_content_json': current_content_json,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'cost': cost,
            'model_used': model_name,
            'error': error_msg,
        }

    async def send_message_with_retry(self, chat, text: str, status_callback=None) -> dict:
        """Sends a message with exponential backoff, jitter, and fallback model switching."""
        response = None
        
        retryer = AsyncRetrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception(lambda e: isinstance(e, (APIError, ClientError)) and not _is_rate_limit(e)),
            reraise=True
        )

        async for attempt in retryer:
            with attempt:
                attempt_number = attempt.retry_state.attempt_number

                # If we fail 4 times, switch the chat model for the fallback
                if attempt_number == 5:
                    logger.info("Switching to fallback model gemini-2.5-flash...")
                    chat._model = 'gemini-2.5-flash'

                if attempt_number > 1 and status_callback:
                    msg = f"⏳ *Model busy* \\(Attempt {attempt_number}/5\\)\\. Retrying\\.\\.\\."
                    try:
                        await status_callback(msg)
                    except Exception as e:
                        logger.error(f"Callback failed: {e}")

                # Chat objects are bound to a single client so we can't rotate keys;
                # allow_key_rotation=False makes _execute_with_ratelimit wait instead.
                response = await self._execute_with_ratelimit(
                    lambda: chat.send_message(text),
                    allow_key_rotation=False,
                )

        response_text = response.text if response else None
        
        grounding_sources = []
        if response and getattr(response, 'candidates', None) and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if getattr(candidate, 'grounding_metadata', None) and getattr(candidate.grounding_metadata, 'grounding_chunks', None):
                for chunk in candidate.grounding_metadata.grounding_chunks:
                    if getattr(chunk, 'web', None):
                        title = getattr(chunk.web, 'title', 'Source')
                        uri = getattr(chunk.web, 'uri', '')
                        if uri:
                            grounding_sources.append({'title': title, 'uri': uri})

        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        if response and getattr(response, 'usage_metadata', None):
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0
            completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) or 0) + thinking_tokens
            cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0

        error_msg = None
        if response_text is None:
            logger.error(f"Gemini returned an empty/blocked response: {response}")
            error_msg = "Gemini returned an empty or blocked response."
            response_text = "⚠️ Gemini returned an empty or blocked response (likely triggered safety filters). Please check the JD and try again."

        return {
            'text': response_text,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'cached_tokens': cached_tokens,
            'cost': self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=chat._model),
            'model_used': chat._model,
            'error': error_msg,
            'grounding_sources': grounding_sources
        }

    def is_final_output(self, raw: str) -> bool:
        """Check if the response contains the final structured markers."""
        if not raw: return False
        return "===COMPANY_NAME===" in raw and "===TAILORED_HTML===" in raw

    def parse_final_response(self, raw: str) -> dict:
        def extract(tag):
            pattern = rf'==={tag}===\s*(.*?)(?====\w+===|$)'
            m = re.search(pattern, raw, re.DOTALL)
            return m.group(1).strip() if m else ''

        company = extract('COMPANY_NAME')
        html = extract('TAILORED_HTML')
        cover_letter = extract('COVER_LETTER')

        # Strip code fences if Gemini wrapped HTML in ```html ... ```
        html = re.sub(r'^```html\s*', '', html, flags=re.MULTILINE)
        html = re.sub(r'^```\s*$', '', html, flags=re.MULTILINE)

        if not company:
            company = 'UnknownCompany'
        if not html:
            raise ValueError('Gemini did not return tailored HTML.\nRaw: ' + raw[:500])

        # Build display name by inserting spaces before caps (e.g. AlignTechnology → Align Technology)
        display = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', company)

        return {
            'company_name': re.sub(r'[^\w]', '', company),
            'company_name_display': display,
            'tailored_html': html,
            'cover_letter': cover_letter,
        }

    async def evaluate_resume(self, current_content_json: str, master_profile_json: str, jd: str, company_type: str, jd_keywords: str = '') -> tuple[ResumeEvaluation, dict]:
        """Evaluates a tailored resume draft (as TailoredResumeContent JSON) against
        the master profile and JD.  HTML stripping is no longer needed — the generator
        now returns structured JSON, not HTML.
        """
        jd = _truncate_jd_boilerplate(jd)
        system_prompt, contents = build_evaluator_prompt(company_type, master_profile_json, jd, current_content_json, jd_keywords=jd_keywords)

        model_name = 'gemini-2.5-flash'
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
            max_output_tokens=8192,  # matches generator ceiling — evaluation JSON + advisory bullets can be verbose
            thinking_config=GeminiClient._make_thinking_config(model_name, 'low'),
            response_mime_type="application/json",
            response_schema=ResumeEvaluation,
        )

        logger.info(f"Starting Evaluator Agent ({model_name}) for company_type: {company_type}...")

        retryer = AsyncRetrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception(
                lambda e: (isinstance(e, (APIError, ClientError)) and not _is_rate_limit(e))
                          or isinstance(e, ValueError)
            ),
            reraise=True
        )

        response = None
        parsed_evaluation = None

        async for attempt in retryer:
            with attempt:
                if attempt.retry_state.attempt_number == 5:
                    model_name = 'gemini-3.5-flash'
                    logger.info(f"Evaluator: Switching to fallback model {model_name}...")
                    config = types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.2,
                        max_output_tokens=8192,
                        thinking_config=GeminiClient._make_thinking_config(model_name, 'low'),
                        response_mime_type="application/json",
                        response_schema=ResumeEvaluation,
                    )
                response = await self._execute_with_ratelimit(
                    lambda: self.client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config
                    )
                )
                
                if not response or not response.text:
                    raise ValueError("Evaluator returned an empty response.")
                    
                try:
                    text = response.text.strip()
                    if text.startswith('```json'):
                        text = text[7:]
                    if text.endswith('```'):
                        text = text[:-3]
                    text = text.strip()
                    
                    data = json_repair.loads(text)
                    if isinstance(data, str):
                        data = json.loads(data)
                    parsed_evaluation = ResumeEvaluation(**data)

                except Exception as e:
                    finish_reason = "UNKNOWN"
                    if response and getattr(response, 'candidates', None) and len(response.candidates) > 0:
                        finish_reason = str(response.candidates[0].finish_reason)
                        
                    logger.error(f"JSON Decode Error in Evaluator (Finish Reason: {finish_reason}). Raw response:\n{response.text}")
                    
                    # Log the failed LLM request so the user can inspect it in the database
                    prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) if response and getattr(response, 'usage_metadata', None) else 0
                    thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) if response and getattr(response, 'usage_metadata', None) else 0
                    completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) if response and getattr(response, 'usage_metadata', None) else 0) + thinking_tokens
                    cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) if response and getattr(response, 'usage_metadata', None) else 0
                    cost = self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=model_name)

                    if getattr(self, 'session_id', None):
                        # Use asyncio.create_task to run this in the background since we're about to raise
                        import asyncio
                        asyncio.create_task(analytics_logger.log_llm_request(
                            self.session_id, 'critic_evaluator', model_name,
                            prompt_tokens, completion_tokens, cost, cached_tokens=cached_tokens, error=f"FinishReason: {finish_reason} | Error: {e}"
                        ))
                        asyncio.create_task(analytics_logger.log_agent_trace(
                            self.session_id, 0, 'evaluator_failed', 
                            prompt_text="Evaluating current_content_json", 
                            raw_response=response.text if response else "", 
                            parsed_output=f"FinishReason: {finish_reason} | Error: {e}"
                        ))
                        
                    raise ValueError(f"Failed to parse JSON (Finish Reason: {finish_reason}): {e}")

        if not parsed_evaluation:
            raise RuntimeError("Evaluator failed to generate a valid evaluation.")

        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        if response and getattr(response, 'usage_metadata', None):
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0
            completion_tokens = (getattr(response.usage_metadata, 'candidates_token_count', 0) or 0) + thinking_tokens
            cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0

        cost = self._calculate_cost(prompt_tokens, completion_tokens, cached_tokens, model=model_name)
        if getattr(self, 'session_id', None):
            await analytics_logger.log_llm_request(self.session_id, 'critic_evaluator', model_name, prompt_tokens, completion_tokens, cost, cached_tokens=cached_tokens)

        usage = {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'cost': cost,
            'model_used': model_name
        }

        return parsed_evaluation, usage
