```python
"""
AI Text-to-Quiz Generator Backend

API:
    GET  /api/health
    POST /api/generate

POST /api/generate body:
{
    "text": "at least 100 words...",
    "type": "MCQ",
    "count": 10
}

Supported question types:
    MCQ
    FIB
    T/F
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
MAX_INPUT_WORDS = int(os.getenv("MAX_INPUT_WORDS", "12000"))
MIN_INPUT_WORDS = int(os.getenv("MIN_INPUT_WORDS", "100"))
MAX_QUESTIONS = int(os.getenv("MAX_QUESTIONS", "15"))

# Optional comma-separated frontend origins.
# Example:
# ALLOWED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "").strip()

if _allowed_origins:
    ALLOWED_ORIGINS = [
        origin.strip()
        for origin in _allowed_origins.split(",")
        if origin.strip()
    ]
else:
    # Development-friendly default.
    ALLOWED_ORIGINS = "*"


# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB request limit

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": ALLOWED_ORIGINS,
        }
    },
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("text-to-quiz")


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

client: Optional[OpenAI] = None

if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

QuestionType = Literal["MCQ", "FIB", "T/F"]


class GenerateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    type: QuestionType = "MCQ"
    count: int = Field(default=5, ge=1, le=MAX_QUESTIONS)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.replace("\x00", " ")
        value = re.sub(r"\s+", " ", value).strip()

        if not value:
            raise ValueError("Input text cannot be empty.")

        return value


class QuizQuestion(BaseModel):
    question: str
    options: List[str] = Field(default_factory=list)
    answer: str
    explanation: str


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def word_count(text: str) -> int:
    """Count whitespace-separated words."""
    return len(re.findall(r"\S+", text))


def normalize_text(text: str) -> str:
    """Normalize whitespace without destroying punctuation."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text: str) -> List[str]:
    """
    Lightweight sentence splitter.

    This intentionally avoids requiring NLTK/spaCy just to run the backend.
    """
    text = normalize_text(text)

    sentences = re.split(
        r"(?<=[.!?])\s+(?=[A-Z0-9\"'])",
        text,
    )

    return [
        sentence.strip()
        for sentence in sentences
        if len(sentence.strip()) >= 25
    ]


def clean_llm_output(text: str) -> str:
    """
    Remove markdown code fences if a model returns them despite the
    JSON-only instruction.
    """
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    return text.strip()


def extract_json(text: str) -> Any:
    """
    Parse JSON from the model response.

    First tries direct JSON parsing. If the model wrapped the JSON in
    additional text, locate the first valid JSON array.
    """
    text = clean_llm_output(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()

    for match in re.finditer(r"\[", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            return value
        except json.JSONDecodeError:
            continue

    raise ValueError("The AI response did not contain valid JSON.")


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def build_prompt(
    source_text: str,
    question_type: QuestionType,
    count: int,
) -> str:
    """
    Build a strict source-grounded generation prompt.
    """

    if question_type == "MCQ":
        schema = """
{
  "question": "string",
  "options": ["string", "string", "string", "string"],
  "answer": "exactly one option string",
  "explanation": "short explanation grounded in the source"
}
"""

        type_rules = """
MCQ RULES:
- Exactly 4 options.
- Exactly one option must be correct.
- The answer must exactly match one of the four option strings.
- Distractors must be plausible but contradicted or unsupported by the source.
- Do not use "all of the above" or "none of the above".
"""

    elif question_type == "FIB":
        schema = """
{
  "question": "string containing exactly one blank represented as □",
  "options": [],
  "answer": "the missing word or short phrase",
  "explanation": "short explanation grounded in the source"
}
"""

        type_rules = """
FILL-IN-THE-BLANK RULES:
- The question must contain exactly one blank symbol: □
- The answer must be a word or short phrase explicitly supported by the source.
- Do not create an answer that requires outside knowledge.
- options must be an empty array.
"""

    else:
        schema = """
{
  "question": "string",
  "options": [],
  "answer": "True",
  "explanation": "short explanation grounded in the source"
}
"""

        type_rules = """
TRUE/FALSE RULES:
- The answer must be exactly "True" or "False".
- The statement must be objectively verifiable from the source.
- Do not use ambiguous wording.
- options must be an empty array.
"""

    return f"""
You are an expert educational assessment designer.

Create exactly {count} {question_type} questions from the source material below.

IMPORTANT SOURCE RULE:
Every question and answer must be supported by the supplied source.
Do not rely on general knowledge when the source does not establish the fact.

GENERAL RULES:
- Return exactly {count} questions.
- Every question must be self-contained.
- Avoid duplicate questions.
- Avoid trivial questions when a deeper conceptual question is possible.
- Do not mention "according to the passage".
- Do not invent facts.
- Keep wording clear and suitable for high-school/college learners.
- Explanations should be concise.
- Return ONLY valid JSON.
- Return a JSON array.
- Do not wrap the JSON in Markdown.

{type_rules}

Each question must follow this structure:

{schema}

SOURCE MATERIAL:
{source_text}
"""


# ---------------------------------------------------------------------------
# AI generation
# ---------------------------------------------------------------------------

def call_openai(
    prompt: str,
    count: int,
) -> List[Dict[str, Any]]:
    """
    Generate questions through the OpenAI Responses API.
    """

    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    last_error: Optional[Exception] = None

    for attempt in range(2):
        try:
            response = client.responses.create(
                model=LLM_MODEL,
                input=prompt,
                temperature=0.2,
                max_output_tokens=max(1200, count * 350),
            )

            raw = response.output_text.strip()

            if not raw:
                raise ValueError("The AI returned an empty response.")

            data = extract_json(raw)

            if not isinstance(data, list):
                raise ValueError("AI output must be a JSON array.")

            return data

        except Exception as exc:
            last_error = exc
            logger.exception(
                "AI generation attempt %s/%s failed",
                attempt + 1,
                2,
            )

            if attempt == 0:
                time.sleep(0.75)

    raise RuntimeError("AI generation failed.") from last_error


# ---------------------------------------------------------------------------
# Question validation
# ---------------------------------------------------------------------------

def validate_question(
    question: Dict[str, Any],
    question_type: QuestionType,
) -> Optional[Dict[str, Any]]:
    """
    Validate and normalize one generated question.

    Returns a normalized dictionary or None if invalid.
    """

    if not isinstance(question, dict):
        return None

    raw_question = question.get("question")
    raw_options = question.get("options", [])
    raw_answer = question.get("answer")
    raw_explanation = question.get("explanation", "")

    if not isinstance(raw_question, str):
        return None

    if not isinstance(raw_answer, str):
        return None

    if not isinstance(raw_explanation, str):
        raw_explanation = str(raw_explanation)

    question_text = raw_question.strip()
    answer = raw_answer.strip()
    explanation = raw_explanation.strip()

    if not question_text or not answer:
        return None

    # ---------------------------------------------------------------
    # MCQ
    # ---------------------------------------------------------------

    if question_type == "MCQ":
        if not isinstance(raw_options, list):
            return None

        options = [
            str(option).strip()
            for option in raw_options
            if str(option).strip()
        ]

        # Exactly four unique options.
        if len(options) != 4:
            return None

        normalized = [option.casefold() for option in options]

        if len(set(normalized)) != 4:
            return None

        if answer.casefold() not in set(normalized):
            return None

        # Normalize answer to the exact option spelling.
        for option in options:
            if option.casefold() == answer.casefold():
                answer = option
                break

        return {
            "question": question_text,
            "options": options,
            "answer": answer,
            "explanation": explanation
            or "The answer is supported by the supplied source material.",
        }

    # ---------------------------------------------------------------
    # Fill in the blank
    # ---------------------------------------------------------------

    if question_type == "FIB":
        if raw_options not in ([], None):
            return None

        # Exactly one blank.
        if question_text.count("□") != 1:
            return None

        if not explanation:
            explanation = "The answer is supported by the supplied source material."

        return {
            "question": question_text,
            "options": [],
            "answer": answer,
            "explanation": explanation,
        }

    # ---------------------------------------------------------------
    # True / False
    # ---------------------------------------------------------------

    if question_type == "T/F":
        if raw_options not in ([], None):
            return None

        if answer.casefold() not in {"true", "false"}:
            return None

        answer = "True" if answer.casefold() == "true" else "False"

        return {
            "question": question_text,
            "options": [],
            "answer": answer,
            "explanation": explanation
            or "The statement can be evaluated directly from the supplied source material.",
        }

    return None


def validate_questions(
    questions: Any,
    question_type: QuestionType,
    count: int,
) -> List[Dict[str, Any]]:
    """
    Validate generated questions and ensure the requested quantity exists.
    """

    if not isinstance(questions, list):
        raise ValueError("Generated questions are not a list.")

    validated: List[Dict[str, Any]] = []

    seen_questions = set()

    for raw_question in questions:
        normalized = validate_question(raw_question, question_type)

        if normalized is None:
            continue

        key = re.sub(
            r"\s+",
            " ",
            normalized["question"].casefold(),
        )

        if key in seen_questions:
            continue

        seen_questions.add(key)
        validated.append(normalized)

        if len(validated) == count:
            break

    if len(validated) != count:
        raise ValueError(
            f"AI returned only {len(validated)} valid questions; "
            f"{count} were requested."
        )

    return validated


# ---------------------------------------------------------------------------
# Offline fallback
# ---------------------------------------------------------------------------

def fallback_generate(
    text: str,
    question_type: QuestionType,
    count: int,
) -> List[Dict[str, Any]]:
    """
    Source-grounded offline fallback.

    This is intentionally conservative. It does not pretend to generate
    sophisticated AI questions. It creates simple questions from actual
    sentences in the supplied text.
    """

    sentences = split_sentences(text)

    if not sentences:
        raise ValueError(
            "The source text does not contain enough usable sentences."
        )

    # Use different source sentences where possible.
    selected: List[str] = []

    if len(sentences) <= count:
        selected = sentences
    else:
        indexes = [
            round(i * (len(sentences) - 1) / (count - 1))
            if count > 1
            else 0
            for i in range(count)
        ]
        selected = [sentences[index] for index in indexes]

    results: List[Dict[str, Any]] = []

    for sentence in selected:
        sentence = sentence.strip()

        # -----------------------------------------------------------
        # FIB fallback
        # -----------------------------------------------------------

        if question_type == "FIB":
            words = re.findall(
                r"\b[A-Za-z][A-Za-z0-9-]{4,}\b",
                sentence,
            )

            if not words:
                continue

            answer = words[0]

            question = re.sub(
                rf"\b{re.escape(answer)}\b",
                "□",
                sentence,
                count=1,
            )

            results.append(
                {
                    "question": question,
                    "options": [],
                    "answer": answer,
                    "explanation": (
                        f"The missing term is '{answer}', "
                        "which appears in the supplied source."
                    ),
                }
            )

        # -----------------------------------------------------------
        # T/F fallback
        # -----------------------------------------------------------

        elif question_type == "T/F":
            results.append(
                {
                    "question": sentence,
                    "options": [],
                    "answer": "True",
                    "explanation": (
                        "The statement is directly supported by "
                        "the supplied source."
                    ),
                }
            )

        # -----------------------------------------------------------
        # MCQ fallback
        # -----------------------------------------------------------

        else:
            # A conservative MCQ is better than fabricated distractors.
            words = re.findall(
                r"\b[A-Za-z][A-Za-z0-9-]{4,}\b",
                sentence,
            )

            if not words:
                continue

            answer = words[0]

            # Extract other long words from the source as distractors.
            candidates = []

            for word in re.findall(
                r"\b[A-Za-z][A-Za-z0-9-]{4,}\b",
                text,
            ):
                if word.casefold() != answer.casefold():
                    if word.casefold() not in {
                        candidate.casefold()
                        for candidate in candidates
                    }:
                        candidates.append(word)

                if len(candidates) >= 3:
                    break

            if len(candidates) < 3:
                continue

            options = [answer] + candidates[:3]

            results.append(
                {
                    "question": (
                        f"Which term from the source appears in this "
                        f"statement?\n\n{sentence}"
                    ),
                    "options": options,
                    "answer": answer,
                    "explanation": (
                        f"'{answer}' is explicitly present in the "
                        "supplied source sentence."
                    ),
                }
            )

        if len(results) == count:
            break

    if len(results) < count:
        raise ValueError(
            "The offline fallback could not create enough questions "
            "from the supplied text."
        )

    return results


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Simple health/readiness endpoint."""

    return jsonify(
        {
            "ok": True,
            "service": "text-to-quiz",
            "llm_configured": client is not None,
            "model": LLM_MODEL if client is not None else None,
        }
    )


@app.post("/api/generate")
def generate():
    """Generate a quiz from source text."""

    # Do not use force=True; reject malformed/non-JSON requests.
    if not request.is_json:
        return jsonify(
            {
                "ok": False,
                "error": "Content-Type must be application/json.",
            }
        ), 415

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify(
            {
                "ok": False,
                "error": "Request body must be a JSON object.",
            }
        ), 400

    try:
        req = GenerateRequest.model_validate(payload)
    except ValidationError as exc:
        return jsonify(
            {
                "ok": False,
                "error": "Invalid request.",
                "details": exc.errors(),
            }
        ), 400

    text = normalize_text(req.text)
    words = word_count(text)

    if words < MIN_INPUT_WORDS:
        return jsonify(
            {
                "ok": False,
                "error": (
                    f"Input must contain at least "
                    f"{MIN_INPUT_WORDS} words."
                ),
                "word_count": words,
            }
        ), 400

    if words > MAX_INPUT_WORDS:
        return jsonify(
            {
                "ok": False,
                "error": (
                    f"Input is too large. Maximum is "
                    f"{MAX_INPUT_WORDS} words."
                ),
                "word_count": words,
            }
        ), 400

    prompt = build_prompt(
        source_text=text,
        question_type=req.type,
        count=req.count,
    )

    # ---------------------------------------------------------------
    # AI generation
    # ---------------------------------------------------------------

    if client is not None:
        try:
            raw_questions = call_openai(
                prompt=prompt,
                count=req.count,
            )

            questions = validate_questions(
                questions=raw_questions,
                question_type=req.type,
                count=req.count,
            )

            return jsonify(
                {
                    "ok": True,
                    "source": "openai",
                    "model": LLM_MODEL,
                    "question_type": req.type,
                    "count": len(questions),
                    "questions": questions,
                }
            )

        except Exception:
            logger.exception(
                "AI generation failed; attempting offline fallback."
            )

    # ---------------------------------------------------------------
    # Offline fallback
    # ---------------------------------------------------------------

    try:
        questions = fallback_generate(
            text=text,
            question_type=req.type,
            count=req.count,
        )

        return jsonify(
            {
                "ok": True,
                "source": "fallback",
                "question_type": req.type,
                "count": len(questions),
                "questions": questions,
            }
        )

    except Exception as exc:
        logger.exception("Fallback generation failed.")

        return jsonify(
            {
                "ok": False,
                "error": (
                    "Unable to generate the requested quiz. "
                    "Please provide more detailed source material "
                    "or try again."
                ),
                "details": str(exc) if DEBUG else None,
            }
        ), 503


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(
        {
            "ok": False,
            "error": "Request body is too large.",
        }
    ), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify(
        {
            "ok": False,
            "error": "Endpoint not found.",
        }
    ), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify(
        {
            "ok": False,
            "error": "HTTP method not allowed.",
        }
    ), 405


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    logger.exception("Unhandled server error: %s", error)

    return jsonify(
        {
            "ok": False,
            "error": "Internal server error.",
        }
    ), 500


# ---------------------------------------------------------------------------
# Development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=DEBUG,
    )
```
