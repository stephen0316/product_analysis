from pathlib import Path
import subprocess

import uvicorn


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"


def build_frontend() -> None:
    """Build the React app before serving FastAPI's static `frontend/dist` files."""
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)


if __name__ == "__main__":
    build_frontend()
    uvicorn.run("post_evaluation.app:app", host="127.0.0.1", port=8765, reload=False)
