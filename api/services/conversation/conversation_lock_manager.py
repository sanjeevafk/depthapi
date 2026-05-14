"""Conversation-level locking with automatic TTL cleanup."""

import asyncio
import time
from asyncio import Semaphore

from api.logging_config import logger


class ConversationLockManager:
    """
    Manages conversation locks with automatic TTL-based cleanup.
    
    Replaces:
    - Global _CONVERSATION_LOCKS dict
    - _prune_conversation_locks()
    - _acquire_conversation_lock()
    - _release_conversation_lock()
    
    Benefits:
    - Encapsulated state management
    - Automatic stale lock cleanup
    - Configurable TTL
    - Thread-safe operations
    """
    
    def __init__(self, max_locks: int = 10000, ttl_seconds: float = 600):
        """Initialize lock manager.
        
        Args:
            max_locks: Maximum locks to maintain (prunes aggressively if exceeded)
            ttl_seconds: Time-to-live for locks before cleanup
        """
        self._locks: dict[str, tuple[Semaphore, float]] = {}
        self._lock = asyncio.Lock()
        self.max_locks = max_locks
        self.ttl = ttl_seconds
    
    async def acquire(
        self,
        conversation_id: str,
        timeout_seconds: float = 1.0,
    ) -> bool:
        """Acquire conversation lock with timeout.
        
        Args:
            conversation_id: Conversation to lock
            timeout_seconds: Timeout for acquiring lock
            
        Returns:
            True if lock acquired, False if timeout
            
        Raises:
            HTTPException (429) by caller if returns False
        """
        # Get or create semaphore
        async with self._lock:
            now = time.time()
            await self._prune_stale(now)
            
            if conversation_id not in self._locks:
                sem = Semaphore(1)
                self._locks[conversation_id] = (sem, now)
            else:
                sem, _ = self._locks[conversation_id]
                self._locks[conversation_id] = (sem, now)
        
        # Try to acquire semaphore with timeout
        try:
            await asyncio.wait_for(
                sem.acquire(),
                timeout=timeout_seconds,
            )
            
            # Update last_used timestamp
            async with self._lock:
                if conversation_id in self._locks:
                    sem, _ = self._locks[conversation_id]
                    self._locks[conversation_id] = (sem, time.time())
            
            return True
        except asyncio.TimeoutError:
            return False
    
    def release(self, conversation_id: str) -> None:
        """Release conversation lock.
        
        Args:
            conversation_id: Conversation to unlock
        """
        if entry := self._locks.get(conversation_id):
            sem, last_used = entry
            sem.release()
            self._locks[conversation_id] = (sem, time.time())
    
    async def _prune_stale(self, now: float) -> None:
        """Remove expired locks.
        
        Strategy:
        - If under max_locks: Prune locks older than ttl_seconds
        - If over max_locks: Prune locks older than min(ttl, 120s) (aggressive)
        
        Args:
            now: Current time.time()
        """
        if len(self._locks) <= self.max_locks:
            cutoff = now - self.ttl
        else:
            # Aggressive pruning when at capacity
            cutoff = now - min(self.ttl, 120.0)
        
        stale_keys = []
        for key, (sem, last_used) in self._locks.items():
            if last_used >= cutoff:
                continue
            
            # Only remove if semaphore is available (not in use)
            sem_value = getattr(sem, "_value", None)
            if sem_value == 1:
                stale_keys.append(key)
        
        removed_count = 0
        for key in stale_keys:
            self._locks.pop(key, None)
            removed_count += 1
        
        if removed_count > 0 and len(self._locks) > self.max_locks:
            logger.debug(
                "lock_manager_pruned",
                cutoff_seconds=round(now - cutoff, 1),
                removed_count=removed_count,
                remaining_locks=len(self._locks),
            )
