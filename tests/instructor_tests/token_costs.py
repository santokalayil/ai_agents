from instructor.exceptions import IncompleteOutputException
import openai
import instructor
from pydantic import BaseModel

client = instructor.from_openai(openai.OpenAI())


class UserExtract(BaseModel):
    name: str
    age: int


try:
    client.chat.completions.create_with_completion(
        model="gpt-3.5-turbo",
        response_model=UserExtract,
        messages=[
            {"role": "user", "content": "Extract jason is 25 years old"},
        ],
    )
except IncompleteOutputException as e:
    token_count = e.last_completion.usage.total_tokens  # type: ignore
    # your logic here