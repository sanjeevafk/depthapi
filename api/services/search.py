import hashlib
import random
import httpx
from typing import Dict, Any, List, Optional
from config import get_settings
from services.cache import cache_get, cache_set
from logging_config import logger

settings = get_settings()

class SearchManager:
    def __init__(self):
        self.visual_keywords = {"diagram", "flowchart", "image", "photo", "visual", "graph", "chart"}

    async def get_search_context(self, query: str) -> str:
        # Check cache
        try:
            cache_key = f"search:{hashlib.sha256(query.encode()).hexdigest()}"
            cached = await cache_get(cache_key)
            if cached and isinstance(cached, dict) and "content" in cached:
                logger.info("search_cache_hit", query=query)
                return cached["content"]
        except Exception as e:
            logger.warning("cache_error_search", error=str(e))

        # Determine provider
        provider = self._select_provider(query)
        logger.info("search_provider_selected", provider=provider, query=query)

        content = ""
        try:
            if provider == "tavily":
                content = await self._search_tavily(query)
            elif provider == "serper":
                content = await self._search_serper(query)
            elif provider == "exa":
                content = await self._search_exa(query)
        except Exception as e:
            logger.error("search_provider_failed", provider=provider, error=str(e))
            content = await self._fallback_search(query, failed_provider=provider)

        if not content and content is not None:
             # Try fallback if content is empty string (failure)
             content = await self._fallback_search(query, failed_provider=provider)

        # Cache result if valid
        if content:
            await cache_set(cache_key, {"content": content}, ttl=86400) # 24h default

        return content if content else "No external context found."

    def _select_provider(self, query: str) -> str:
        # Check for visual keywords - favor Serper
        if any(keyword in query.lower() for keyword in self.visual_keywords):
            return "serper" if random.random() < 0.7 else self._weighted_random()
        
        return self._weighted_random()

    def _weighted_random(self) -> str:
        # Tavily 50%, Serper 30%, Exa 20%
        rand = random.random()
        if rand < 0.5:
            return "tavily"
        elif rand < 0.8:
            return "serper"
        else:
            return "exa"

    async def _search_tavily(self, query: str) -> str:
        if not settings.tavily_api_key:
            raise ValueError("Tavily API key missing")
        
        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": 5
        }
        async with httpx.AsyncClient(timeout=5.0) as client:  # Reduced from 10s to 5s
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            formatted = "\n".join([f"- {r['title']}: {r['content']} ({r['url']})" for r in results])
            return f"Answer: {data.get('answer', '')}\nSources:\n{formatted}"

    async def _search_serper(self, query: str) -> str:
        if not settings.serper_api_key:
            raise ValueError("Serper API key missing")
        
        headers = {
            'X-API-KEY': settings.serper_api_key,
            'Content-Type': 'application/json'
        }
        async with httpx.AsyncClient(timeout=5.0) as client:  # Reduced from 10s to 5s
            resp = await client.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query}
            )
            resp.raise_for_status()
            data = resp.json()
            organic = data.get("organic", [])
            formatted = "\n".join([f"- {r.get('title')}: {r.get('snippet')} ({r.get('link')})" for r in organic[:5]])
            return formatted

    async def _search_exa(self, query: str) -> str:
        if not settings.exa_api_key:
             raise ValueError("Exa API key missing")
             
        headers = {
            "x-api-key": settings.exa_api_key,
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient(timeout=5.0) as client:  # Reduced from 10s to 5s
            resp = await client.post(
                "https://api.exa.ai/search",
                headers=headers,
                json={"query": query, "numResults": 5, "contents": {"text": True}}
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            formatted = "\n".join([f"- {r.get('title')}: {r.get('text', '')[:300]}... ({r.get('url')})" for r in results])
            return formatted

    async def _fallback_search(self, query: str, failed_provider: str) -> str:
        """Optimized parallel fallback with faster timeout."""
        import asyncio
        
        providers = ["tavily", "serper", "exa"]
        if failed_provider in providers:
            providers.remove(failed_provider)
        
        # Try all remaining providers in parallel for faster fallback
        tasks = []
        for p in providers:
            if p == "tavily":
                tasks.append(self._search_tavily(query))
            elif p == "serper":
                tasks.append(self._search_serper(query))
            elif p == "exa":
                tasks.append(self._search_exa(query))
        
        # Return first successful result
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                if result:  # Return first non-empty result
                    return result
            except Exception as e:
                logger.warning("fallback_provider_failed", error=str(e))
                continue
        
        return ""

    async def get_images(self, query: str) -> List[Dict[str, str]]:
        # Fallback to Serper Images if Unsplash key is missing or logic dictates
        # This implementation uses Serper for simplicity as Unsplash key was not provided
        if not settings.serper_api_key:
            return []
            
        headers = {
            'X-API-KEY': settings.serper_api_key,
            'Content-Type': 'application/json'
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "https://google.serper.dev/images",
                    headers=headers,
                    json={"q": query}
                )
                resp.raise_for_status()
                data = resp.json()
                images = data.get("images", [])
                return [{"url": img["imageUrl"], "title": img["title"]} for img in images[:3]]
        except Exception as e:
            logger.error("image_search_failed", error=str(e))
            return []

    async def get_quote(self) -> str:
        # Simple quote for loading messages
        tags = "education|knowledge|learning|science|wisdom|research|effort|creativity"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"https://api.quotable.io/random?tags={tags}&maxLength=100")
                if resp.status_code == 200:
                    data = resp.json()
                    return f"«{data['content']}» — {data['author']}"
        except:
            pass
        
        fallbacks = [
            "The mind is not a vessel to be filled, but a fire to be kindled. — Plutarch",
            "An investment in knowledge pays the best interest. — Benjamin Franklin",
            "Wisdom is not a product of schooling but of the lifelong attempt to acquire it. — Albert Einstein",
            "The important thing is not to stop questioning. Curiosity has its own reason for existence. — Albert Einstein",
            "Live as if you were to die tomorrow. Learn as if you were to live forever. — Mahatma Gandhi"
        ]
        return random.choice(fallbacks)

    async def get_regeneration_quote(self) -> str:
        """Specialized quote fetching for regenerated answers with style variety."""
        tags = "education|knowledge|learning|science|wisdom|philosophy|technology|research|creativity|innovation|discovery|effort|critical-thinking"
        styles = [
            "---\n*“{content}”* — {author}",
            "---\nAs {author} said: *“{content}”*",
            "---\n*“{content}”*\n— {author}",
            "---\nIn the words of {author}: *“{content}”*",
            "---\nA thought worth keeping: *“{content}”* — {author}"
        ]
        
        fallback_quotes = [
            {"author": "Marie Curie", "content": "Nothing in life is to be feared, it is only to be understood. Now is the time to understand more."},
            {"author": "Richard Feynman", "content": "The first principle is that you must not fool yourself and you are the easiest person to fool."},
            {"author": "Leonardo da Vinci", "content": "Learning never exhausts the mind."},
            {"author": "Srinivasa Ramanujan", "content": "An equation for me has no meaning unless it expresses a thought of God."},
            {"author": "Ada Lovelace", "content": "That brain of mine is something more than merely mortal; as time will show."},
            {"author": "Hypatia", "content": "Reserve your right to think, for even to think wrongly is better than not to think at all."},
            {"author": "Nikola Tesla", "content": "The present is theirs; the future, for which I really worked, is mine."},
            {"author": "Rosalind Franklin", "content": "Science and everyday life cannot and should not be separated."},
            {"author": "Isaac Newton", "content": "If I have seen further it is by standing on the shoulders of Giants."},
            {"author": "Grace Hopper", "content": "The most dangerous phrase in the language is, 'We've always done it this way.'"}
        ]

        # Get session state for variety (author and style index)
        cache_key = "regen_quote_state"
        state = await cache_get(cache_key) or {"last_author": "", "last_style_idx": -1}
        
        quote_data = None
        attempts = 0
        
        async with httpx.AsyncClient(timeout=4.0) as client:
            while attempts < 2:
                attempts += 1
                try:
                    # minLength=50, maxLength=120 as requested
                    resp = await client.get(f"https://api.quotable.io/random?tags={tags}&minLength=50&maxLength=120")
                    if resp.status_code == 200:
                        data = resp.json()
                        # Avoid overused authors if possible
                        overused = ["Albert Einstein", "Plutarch", "Marcus Aurelius", "Socrates", "Benjamin Franklin"]
                        if data["author"] == state.get("last_author") or (data["author"] in overused and attempts == 1):
                            continue # Try again once
                        quote_data = data
                        break
                except:
                    break

        if not quote_data:
            # Fallback rotation
            last_fallback_idx = state.get("last_fallback_idx", -1)
            next_fallback_idx = (last_fallback_idx + 1) % len(fallback_quotes)
            quote_data = fallback_quotes[next_fallback_idx]
            state["last_fallback_idx"] = next_fallback_idx

        # Style rotation
        last_style_idx = state.get("last_style_idx", -1)
        available_styles = [i for i in range(len(styles)) if i != last_style_idx]
        style_idx = random.choice(available_styles)
        
        formatted_quote = styles[style_idx].format(
            content=quote_data["content"].replace("«", "").replace("»", "").strip(), 
            author=quote_data["author"]
        )
        
        # Save state
        state["last_author"] = quote_data["author"]
        state["last_style_idx"] = style_idx
        await cache_set(cache_key, state, ttl=3600)
        
        return formatted_quote

search_service = SearchManager()
