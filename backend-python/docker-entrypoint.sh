#!/bin/bash
set -e

echo "⏳ Waiting for PostgreSQL..."
RETRIES=20
until python -c "
import asyncio, asyncpg, os
async def check():
    raw_url = os.environ.get('DATABASE_URL', '')
    dsn = raw_url.replace('postgresql+asyncpg://', 'postgresql://').replace('ssl=require', 'sslmode=require')
    conn = await asyncpg.connect(dsn=dsn)
    await conn.close()
asyncio.run(check())
" 2>/dev/null || [ $RETRIES -eq 0 ]; do
  echo "  PostgreSQL not ready — retrying in 2s... ($RETRIES attempts left)"
  RETRIES=$((RETRIES-1))
  sleep 2
done

if [ $RETRIES -eq 0 ]; then
  echo "⚠️ PostgreSQL check timed out or bypassed, attempting application startup..."
else
  echo "✅ PostgreSQL is up"
fi

echo "⏳ Checking Redis (optional)..."
REDIS_RETRIES=3
until python -c "
import redis, os
r = redis.from_url(os.environ.get('REDIS_URL', 'redis://redis:6379/0'))
r.ping()
" 2>/dev/null || [ $REDIS_RETRIES -eq 0 ]; do
  REDIS_RETRIES=$((REDIS_RETRIES-1))
  sleep 1
done

if [ $REDIS_RETRIES -eq 0 ]; then
  echo "⚠️ Redis not reachable — continuing without Redis cache"
else
  echo "✅ Redis is up"
fi

echo "🚀 Starting application..."
exec "$@"
