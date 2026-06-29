"""
AI 演讲反馈教练 — Web 服务入口
用法: python run_server.py
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from server.config import HOST, PORT

if __name__ == "__main__":
    import uvicorn
    print("AI Speech Coach Web Server")
    print(f"   Open: http://localhost:{PORT}")
    print(f"   API docs: http://localhost:{PORT}/docs")
    print("   Press Ctrl+C to stop")
    uvicorn.run(
        "server.app:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
