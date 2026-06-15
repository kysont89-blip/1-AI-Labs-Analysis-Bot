"""
AI-Powered Analysis Engine (Optional Bonus)

When Ollama is online and fast, adds AI reasoning on top of code analysis.
When offline or slow, bot falls back gracefully to code analysis.

Uses simple text prompts for reliable cloud model output.
"""

import httpx
import re
from typing import Dict, Optional, List
from dataclasses import dataclass


@dataclass
class AIAnalysis:
    signal: str  # LONG / SHORT / NEUTRAL
    confidence: float  # 0.0 - 1.0
    reasoning: str
    key_levels: Dict[str, List[float]]
    risk_assessment: str
    time_frame_bias: str
    warning: Optional[str] = None
    raw_response: Optional[str] = None


class AIAnalyzer:
    """AI analysis - optional bonus layer."""

    def __init__(self, model: str = None, base_url: str = None):
        import os
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3.5:397b-cloud")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip('/')
        self.client = httpx.AsyncClient(timeout=15.0)  # Short timeout - don't block user

    async def analyze(self,
                      symbol: str,
                      timeframe: str,
                      price: float,
                      indicators: Dict,
                      patterns: List[Dict],
                      fundamental: Optional[Dict] = None,
                      mtf: Optional[Dict] = None) -> AIAnalysis:
        """Quick AI analysis - returns fast or times out."""
        prompt = self._build_prompt(symbol, timeframe, price, indicators, patterns, fundamental, mtf)

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": 0,
            "options": {
                "temperature": 0.2,
                "num_predict": 300  # Low = fast response
            }
        }

        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=15.0
            )
            response.raise_for_status()
            data = response.json()

            msg = data.get("message", {})
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            
            # Prefer content, fallback to thinking
            full_text = content or thinking or ""
            return self._parse_response(full_text)

        except Exception as e:
            return AIAnalysis(
                signal="NEUTRAL",
                confidence=0.0,
                reasoning="",
                key_levels={"support": [], "resistance": []},
                risk_assessment="",
                time_frame_bias="unknown",
                warning=None,
                raw_response=str(e)
            )

    def _build_prompt(self, symbol: str, timeframe: str, price: float,
                     indicators: Dict, patterns: List[Dict],
                     fundamental: Optional[Dict],
                     mtf: Optional[Dict]) -> str:
        """Build super-compact prompt for fast response."""

        trend_score = indicators.get('trend_score', 0)
        rsi = indicators.get('rsi', 50)
        adx = indicators.get('adx', 0)

        trend_dir = "bullish" if trend_score > 0 else "bearish" if trend_score < 0 else "neutral"

        prompt = f"""{symbol} {timeframe}. Price {price:,.2f}. Trend {trend_dir} (score {trend_score:+.0f}). RSI {rsi:.1f}. ADX {adx:.1f}.

Quick trading signal:
Signal: [LONG/SHORT/NEUTRAL]
Confidence: [0.00-1.00]
Reason: [1 sentence]
"""
        return prompt

    def _parse_response(self, text: str) -> AIAnalysis:
        """Parse structured text response."""
        if not text or not text.strip():
            return AIAnalysis(
                signal="NEUTRAL", confidence=0.0,
                reasoning="", key_levels={"support": [], "resistance": []},
                risk_assessment="", time_frame_bias="unknown",
                warning=None, raw_response=None
            )

        signal_match = re.search(r'Signal:\s*(LONG|SHORT|NEUTRAL)', text, re.IGNORECASE)
        confidence_match = re.search(r'Confidence:\s*(0?\.\d+|1\.0|1)', text)
        reason_match = re.search(r'Reason:\s*(.+?)(?=\n|$)', text, re.DOTALL)

        signal = signal_match.group(1).upper() if signal_match else 'NEUTRAL'
        confidence = 0.5
        if confidence_match:
            try:
                confidence = float(confidence_match.group(1))
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                pass

        reasoning = reason_match.group(1).strip() if reason_match else ""

        return AIAnalysis(
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            key_levels={"support": [], "resistance": []},
            risk_assessment="",
            time_frame_bias="unknown",
            warning=None,
            raw_response=None
        )

    async def close(self):
        await self.client.aclose()

    async def ask(
        self,
        messages: List[Dict[str, str]],
        system: str,
        temperature: float = 0.4,
        num_predict: int = 600,
        timeout: float = 30.0,
    ) -> str:
        """Free-form Q&A. Returns raw assistant text. Never raises.

        Reuses the same Ollama endpoint as analyze() but accepts a full
        message list (with prior turns) and a system prompt. The caller
        owns the conversation history.

        On any failure (Ollama offline, timeout, parse error), returns a
        safe user-facing string so the handler doesn't need to try/except.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "stream": False,
            "keep_alive": 0,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            msg = data.get("message", {}) or {}
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            return (content or thinking or "").strip()
        except Exception:
            return "⚠️ AI is offline. Try again in a moment."


if __name__ == '__main__':
    import asyncio
    from unified_market_data import UnifiedDataFetcher
    from indicators import IndicatorCalculator
    from pattern_detector import PatternDetector

    async def test():
        fetcher = UnifiedDataFetcher()
        df = fetcher.get_klines('XAUUSD', 'H1', 50)
        price = float(df['close'].iloc[-1])
        calc = IndicatorCalculator(df)
        indicators = calc.calculate_all()
        detector = PatternDetector(df)
        patterns = detector.detect_all()

        ai = AIAnalyzer()
        result = await ai.analyze('XAUUSD', 'H1', price, indicators.to_dict(), patterns)

        print(f"Signal: {result.signal}")
        print(f"Confidence: {result.confidence:.0%}")
        print(f"Reasoning: {result.reasoning}")
        if result.warning:
            print(f"Warning: {result.warning}")
        await ai.close()

    asyncio.run(test())
