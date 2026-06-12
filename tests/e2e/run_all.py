"""Запуск всех E2E тестов."""
import asyncio
from nexus_flows import test_nexus
from arcana_flows import test_arcana

async def main():
    print("🚀 E2E ТЕСТЫ NEXUS + ARCANA")
    print("="*50)

    await test_nexus()
    print("\n\n")
    await test_arcana()

if __name__ == "__main__":
    asyncio.run(main())
