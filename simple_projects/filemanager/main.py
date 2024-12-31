import sys
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Union
import nest_asyncio
import logging
import dotenv

# setting up jupyter style runs and sys path to access libs from folder
dotenv.load_dotenv(".../.env")
sys.path.append(str(Path(__file__).parent.parent.parent))
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.result import RunResult
from pydantic_ai.models.vertexai import VertexAIModel

# project specific module imports
from models import FileManagementTool

model = VertexAIModel(
    model_name=os.getenv("GEMINI_MODEL"),
    project_id=os.getenv("VERTEX_AI_PROJECT_ID"),
)

filemanger = Agent(model, result_type=FileManagementTool)
result = filemanger.run_sync("Could you please create directory named santo within temp folder of the root dir. ")
actual_result = result.data()

# call function
actual_result()







# print(result.data)
# print(result.all_messages())

# print(100 * "-")


# result = agent.run_sync('who is your favourite cricketer?', message_history=result.all_messages())
# print(result.data)
# print(result.all_messages())
# print(result.new_messages())