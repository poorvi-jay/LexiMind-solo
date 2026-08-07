import os
from pathlib import Path
from groq import Groq
from fastapi import HTTPException
from dotenv import load_dotenv

from backend.services.classifier_service import hard_word_pct as count_hard_words

# Explicit path so .env loads regardless of the process's working directory
# (this module can be imported standalone, e.g. in tests, without main.py running first)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Load Groq client once at module level
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SIMPLIFICATION_PROMPT = """You are an expert reading assistant helping a person with dyslexia read more easily.

Rewrite the following text following these strict rules:
1. Target a Grade 6 reading level — simple, clear, direct sentences.
2. Split any sentence longer than 15 words into two shorter sentences.
3. Replace difficult or uncommon words with simpler everyday synonyms.
4. Keep the exact same meaning — do not add or remove information.
5. Use active voice instead of passive voice wherever possible.
6. Use short paragraphs — maximum 3 sentences per paragraph.
7. Return ONLY the rewritten text, nothing else. No explanations, no notes.

Text to rewrite:
"""

def flesch_kincaid_grade(text: str) -> float:
    """Calculate Flesch-Kincaid grade level"""
    sentences = [s.strip() for s in text.replace('!', '.').replace('?', '.').split('.') if s.strip()]
    words = text.split()

    if not sentences or not words:
        return 0.0

    syllable_count = sum(
        max(1, len([c for c in w.lower() if c in 'aeiou']))
        for w in words
    )

    avg_sentence_length = len(words) / len(sentences)
    avg_syllables_per_word = syllable_count / len(words)

    grade = 0.39 * avg_sentence_length + 11.8 * avg_syllables_per_word - 15.59
    return round(max(0.0, grade), 1)

def get_level_label(grade: float) -> str:
    if grade <= 5:
        return "Easy"
    elif grade <= 8:
        return "Moderate"
    elif grade <= 11:
        return "Hard"
    else:
        return "Very Hard"

async def simplify_text(text: str) -> dict:
    """F42 — Simplify text using Groq LLaMA model"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "user",
                    "content": SIMPLIFICATION_PROMPT + text
                }
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        simplified = response.choices[0].message.content.strip()

        original_hard_pct = count_hard_words(text)
        simplified_hard_pct = count_hard_words(simplified)
        reading_level = flesch_kincaid_grade(simplified)

        return {
            "simplified_text": simplified,
            "reading_level": get_level_label(reading_level),
            "flesch_kincaid_grade": reading_level,
            "original_hard_word_pct": original_hard_pct,
            "simplified_hard_word_pct": simplified_hard_pct,
            "changes_made": original_hard_pct > simplified_hard_pct
        }

    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Simplification unavailable. You can still read the original text."
        )

async def get_complexity(text: str) -> dict:
    """F43 — Text complexity indicator"""
    words = text.split()
    sentences = [s.strip() for s in text.replace('!', '.').replace('?', '.').split('.') if s.strip()]

    grade = flesch_kincaid_grade(text)
    hard_word_pct = count_hard_words(text)
    word_count = len(words)

    # Estimate reading time at 150 WPM (dyslexic average reading speed)
    est_reading_time_s = round((word_count / 150) * 60)

    return {
        "flesch_kincaid_grade": grade,
        "level_label": get_level_label(grade),
        "hard_word_pct": hard_word_pct,
        "word_count": word_count,
        "sentence_count": len(sentences),
        "est_reading_time_s": est_reading_time_s
    }