"""
Vision Analyzer for XOX Analysis Bot
Sends chart images to Ollama vision model for pattern detection.
"""

import httpx
import base64
import json
from typing import Dict, Optional
from pathlib import Path


class VisionAnalyzer:
    """Analyze chart images using Ollama vision model."""

    def __init__(self, model: str = "kimi-k2.6:cloud", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=60.0)

    async def analyze(self, image_bytes: bytes) -> Dict:
        """
        Send chart image to Ollama vision and return structured analysis.

        Returns dict with:
        - trend: str (bullish/bearish/ranging)
        - patterns: list of detected patterns
        - key_levels: support/resistance
        - confidence: float
        """
        # Encode image to base64
        b64_image = base64.b64encode(image_bytes).decode('utf-8')

        prompt = """You are a professional chart analyst. Analyze this trading chart and respond ONLY with valid JSON in this exact format:

{
  "trend": "bullish|bearish|ranging",
  "trend_confidence": 0.0-1.0,
  "patterns": [
    {
      "name": "pattern name",
      "direction": "bullish|bearish",
      "confidence": 0.0-1.0,
      "location": "where on chart"
    }
  ],
  "key_levels": {
    "support": [price1, price2],
    "resistance": [price1, price2]
  },
  "bias": "long|short|neutral",
  "reasoning": "brief explanation"
}

Rules:
- Be objective. If unclear, say ranging/neutral.
- Only include patterns you are confident about (confidence > 0.60)
- Key levels should be specific price levels visible on chart
- Keep reasoning under 100 words
"""

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image]
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 800
            }
        }

        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            # Parse response
            content = data.get("message", {}).get("content", "")
            return self._parse_response(content)

        except Exception as e:
            return {
                "error": str(e),
                "trend": "unknown",
                "trend_confidence": 0.0,
                "patterns": [],
                "key_levels": {"support": [], "resistance": []},
                "bias": "neutral",
                "reasoning": f"Vision analysis failed: {e}"
            }

    def _parse_response(self, content: str) -> Dict:
        """Parse JSON from model response."""
        # Try to extract JSON block
        import re

        # Find JSON between braces
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Fallback: return raw content for debugging
        return {
            "trend": "unknown",
            "trend_confidence": 0.0,
            "patterns": [],
            "key_levels": {"support": [], "resistance": []},
            "bias": "neutral",
            "reasoning": content[:200],
            "raw": content
        }

    async def close(self):
        await self.client.aclose()


# Synchronous wrapper for non-async contexts
class VisionAnalyzerSync:
    """Synchronous wrapper for VisionAnalyzer."""

    def __init__(self, model: str = "kimi-k2.6:cloud", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip('/')

    def analyze(self, image_bytes: bytes) -> Dict:
        """Synchronous analysis."""
        import requests

        b64_image = base64.b64encode(image_bytes).decode('utf-8')

        prompt = """You are a professional chart analyst. Analyze this trading chart and respond ONLY with valid JSON in this exact format:

{
  "trend": "bullish|bearish|ranging",
  "trend_confidence": 0.0-1.0,
  "patterns": [
    {
      "name": "pattern name",
      "direction": "bullish|bearish",
      "confidence": 0.0-1.0,
      "location": "where on chart"
    }
  ],
  "key_levels": {
    "support": [price1, price2],
    "resistance": [price1, price2]
  },
  "bias": "long|short|neutral",
  "reasoning": "brief explanation"
}

Rules:
- Be objective. If unclear, say ranging/neutral.
- Only include patterns you are confident about (confidence > 0.60)
- Key levels should be specific price levels visible on chart
- Keep reasoning under 100 words
"""

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image]
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 800
            }
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            data = response.json()

            content = data.get("message", {}).get("content", "")
            return self._parse_response(content)

        except Exception as e:
            return {
                "error": str(e),
                "trend": "unknown",
                "trend_confidence": 0.0,
                "patterns": [],
                "key_levels": {"support": [], "resistance": []},
                "bias": "neutral",
                "reasoning": f"Vision analysis failed: {e}"
            }

    def _parse_response(self, content: str) -> Dict:
        import re, json

        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {
            "trend": "unknown",
            "trend_confidence": 0.0,
            "patterns": [],
            "key_levels": {"support": [], "resistance": []},
            "bias": "neutral",
            "reasoning": content[:200],
            "raw": content
        }


# Test
if __name__ == '__main__':
    import asyncio

    async def test():
        # Test with chart_generator
        from chart_generator import ChartGenerator, test_chart
        img = test_chart()

        analyzer = VisionAnalyzer()
        result = await analyzer.analyze(img)
        print(json.dumps(result, indent=2))
        await analyzer.close()

    asyncio.run(test())
