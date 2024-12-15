import sys
import os
from pathlib import Path
import nest_asyncio
import logging
import dotenv

# setting up jupyter style runs and sys path to access libs from folder
dotenv.load_dotenv(".../.env")
sys.path.append(str(Path(__file__).parent.parent.parent))
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

from pydantic_ai import Agent
from pydantic_ai.models.vertexai import VertexAIModel


model = VertexAIModel(
    model_name=os.getenv("GEMINI_MODEL"),
    project_id=os.getenv("VERTEX_AI_PROJECT_ID"),
)
agent = Agent(
    model, result_type=str, system_prompt="You are extremly sarcastic joke maker"
)
result = agent.run_sync('how old are you Gemini?')
print(result.data)



