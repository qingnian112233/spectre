"""DeepSeek Bot 启动入口"""
import asyncio, os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

if not os.getenv("DEEPSEEK_API_KEY"):
    print("❌ 请设置 DEEPSEEK_API_KEY")
    sys.exit(1)
# 用户账号模式不需要 BOT_TOKEN

from .bot import main
asyncio.run(main())
