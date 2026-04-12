"""Запуск всех E2E тестов."""
import asyncio
from test_nexus import test_nexus
from test_arcana import test_arcana

async def main():
    print("🚀 E2E ТЕСТЫ NEXUS + ARCANA")
    print("="*50)

    await test_nexus()
    print("\n\n")
    await test_arcana()

if __name__ == "__main__":
    asyncio.run(main())
