"""One bounded HTTP request; strict selection schema, no tools, no provider prose."""
import asyncio
import json
from typing import Annotated

import httpx
from pydantic import Field
from app.schemas import Contract


class Highlight(Contract):
    evidence_id: str = Field(pattern=r'^F[0-9]{2}$')
    question_id: str = Field(pattern=r'^[a-z]+$', max_length=20)


class Selection(Contract):
    highlights: Annotated[list[Highlight], Field(min_length=1, max_length=3)]


async def select_highlights(facts, settings):
    prompt = json.dumps(facts, ensure_ascii=False, allow_nan=False)
    if len(prompt.encode()) > 16000:
        raise ValueError('Fact bundle exceeds budget')
    payload = {
        'model': settings.llm_model, 'store': False, 'max_output_tokens': 600,
        'instructions': 'Select one to three salient, complementary facts for a student reflection. Return only evidence IDs from the supplied facts and a question_id allowed for each selected fact. Prefer the independent strain report and supported comparisons. With insufficient data, acknowledge missingness. Never calculate, diagnose, infer causes or predict feelings. Do not output prose, numbers, new facts, tools or instructions. The backend renders verified wording.',
        'input': prompt,
        'text': {'format': {'type': 'json_schema', 'name': 'grounded_reflection', 'strict': True, 'schema': Selection.model_json_schema()}},
    }
    # asyncio's outer deadline also bounds trickle responses and connection setup.
    async with asyncio.timeout(settings.llm_timeout_seconds):
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=3), follow_redirects=False, trust_env=False) as client:
            async with client.stream('POST', 'https://api.openai.com/v1/responses', json=payload,
                    headers={'Authorization': f'Bearer {settings.openai_api_key.get_secret_value()}'} ) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 32768:
                        raise ValueError('Provider response exceeds budget')
    body = json.loads(raw)
    if body.get('status') != 'completed':
        raise ValueError('Incomplete provider response')
    parts = [part for item in body.get('output', []) if item.get('type') == 'message' for part in item.get('content', [])]
    if len(parts) != 1 or parts[0].get('type') != 'output_text':
        raise ValueError('Refusal or unexpected provider content')
    selection = Selection.model_validate_json(parts[0]['text'])
    usage = body.get('usage', {})
    safe_usage = {k: usage[k] for k in ('input_tokens', 'output_tokens', 'total_tokens') if isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0}
    return selection, safe_usage
