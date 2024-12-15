from __future__ import annotations as _annotations

import os
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, Union

import pydantic
import pydantic_core
from httpx import USE_CLIENT_DEFAULT, AsyncClient as AsyncHTTPClient, Response as HTTPResponse
from typing_extensions import NotRequired, TypedDict, TypeGuard, assert_never

from .. import UnexpectedModelBehavior, _utils, exceptions, result
from ..messages import (
    ArgsDict,
    Message,
    ModelResponse,
    ModelResponsePart,
    RetryPrompt,
    SystemPrompt,
    TextPart,
    ToolCallPart,
    ToolReturn,
    UserPrompt,
)
from ..settings import ModelSettings
from ..tools import ToolDefinition
from . import (
    AgentModel,
    EitherStreamedResponse,
    Model,
    StreamStructuredResponse,
    StreamTextResponse,
    cached_async_http_client,
    check_allow_model_requests,
    get_user_agent,
)

GeminiModelName = Literal[
    'gemini-1.5-flash', 'gemini-1.5-flash-8b', 'gemini-1.5-pro', 'gemini-1.0-pro', 'gemini-2.0-flash-exp'
]
"""Named Gemini models.

See [the Gemini API docs](https://ai.google.dev/gemini-api/docs/models/gemini#model-variations) for a full list.
"""


@dataclass(init=False)
class GeminiModel(Model):
    """A model that uses Gemini via `generativelanguage.googleapis.com` API.

    This is implemented from scratch rather than using a dedicated SDK, good API documentation is
    available [here](https://ai.google.dev/api).

    Apart from `__init__`, all methods are private or match those of the base class.
    """

    model_name: GeminiModelName
    auth: AuthProtocol
    http_client: AsyncHTTPClient
    url: str

    def __init__(
        self,
        model_name: GeminiModelName,
        *,
        api_key: str | None = None,
        http_client: AsyncHTTPClient | None = None,
        url_template: str = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:',
    ):
        """Initialize a Gemini model.

        Args:
            model_name: The name of the model to use.
            api_key: The API key to use for authentication, if not provided, the `GEMINI_API_KEY` environment variable
                will be used if available.
            http_client: An existing `httpx.AsyncClient` to use for making HTTP requests.
            url_template: The URL template to use for making requests, you shouldn't need to change this,
                docs [here](https://ai.google.dev/gemini-api/docs/quickstart?lang=rest#make-first-request),
                `model` is substituted with the model name, and `function` is added to the end of the URL.
        """
        self.model_name = model_name
        if api_key is None:
            if env_api_key := os.getenv('GEMINI_API_KEY'):
                api_key = env_api_key
            else:
                raise exceptions.UserError('API key must be provided or set in the GEMINI_API_KEY environment variable')
        self.auth = ApiKeyAuth(api_key)
        self.http_client = http_client or cached_async_http_client()
        self.url = url_template.format(model=model_name)

    async def agent_model(
        self,
        *,
        function_tools: list[ToolDefinition],
        allow_text_result: bool,
        result_tools: list[ToolDefinition],
    ) -> GeminiAgentModel:
        return GeminiAgentModel(
            http_client=self.http_client,
            model_name=self.model_name,
            auth=self.auth,
            url=self.url,
            function_tools=function_tools,
            allow_text_result=allow_text_result,
            result_tools=result_tools,
        )

    def name(self) -> str:
        return self.model_name


class AuthProtocol(Protocol):
    """Abstract definition for Gemini authentication."""

    async def headers(self) -> dict[str, str]: ...


@dataclass
class ApiKeyAuth:
    """Authentication using an API key for the `X-Goog-Api-Key` header."""

    api_key: str

    async def headers(self) -> dict[str, str]:
        # https://cloud.google.com/docs/authentication/api-keys-use#using-with-rest
        return {'X-Goog-Api-Key': self.api_key}


@dataclass(init=False)
class GeminiAgentModel(AgentModel):
    """Implementation of `AgentModel` for Gemini models."""

    http_client: AsyncHTTPClient
    model_name: GeminiModelName
    auth: AuthProtocol
    tools: _GeminiTools | None
    tool_config: _GeminiToolConfig | None
    url: str

    def __init__(
        self,
        http_client: AsyncHTTPClient,
        model_name: GeminiModelName,
        auth: AuthProtocol,
        url: str,
        function_tools: list[ToolDefinition],
        allow_text_result: bool,
        result_tools: list[ToolDefinition],
    ):
        check_allow_model_requests()
        tools = [_function_from_abstract_tool(t) for t in function_tools]
        if result_tools:
            tools += [_function_from_abstract_tool(t) for t in result_tools]

        if allow_text_result:
            tool_config = None
        else:
            tool_config = _tool_config([t['name'] for t in tools])

        self.http_client = http_client
        self.model_name = model_name
        self.auth = auth
        self.tools = _GeminiTools(function_declarations=tools) if tools else None
        self.tool_config = tool_config
        self.url = url

    async def request(
        self, messages: list[Message], model_settings: ModelSettings | None
    ) -> tuple[ModelResponse, result.Cost]:
        async with self._make_request(messages, False, model_settings) as http_response:
            response = _gemini_response_ta.validate_json(await http_response.aread())
        return self._process_response(response), _metadata_as_cost(response)

    @asynccontextmanager
    async def request_stream(
        self, messages: list[Message], model_settings: ModelSettings | None
    ) -> AsyncIterator[EitherStreamedResponse]:
        async with self._make_request(messages, True, model_settings) as http_response:
            yield await self._process_streamed_response(http_response)

    @asynccontextmanager
    async def _make_request(
        self, messages: list[Message], streamed: bool, model_settings: ModelSettings | None
    ) -> AsyncIterator[HTTPResponse]:
        sys_prompt_parts: list[_GeminiTextPart] = []
        contents: list[_GeminiContent] = []
        for m in messages:
            if (sys_prompt := self._message_to_gemini_system_prompt(m)) is not None:
                sys_prompt_parts.append(sys_prompt)
            if (content := self._message_to_gemini_content(m)) is not None:
                contents.append(content)

        request_data = _GeminiRequest(contents=contents)
        if sys_prompt_parts:
            request_data['system_instruction'] = _GeminiTextContent(role='user', parts=sys_prompt_parts)
        if self.tools is not None:
            request_data['tools'] = self.tools
        if self.tool_config is not None:
            request_data['tool_config'] = self.tool_config

        generation_config: _GeminiGenerationConfig = {}
        if model_settings:
            if (max_tokens := model_settings.get('max_tokens')) is not None:
                generation_config['max_output_tokens'] = max_tokens
            if (temperature := model_settings.get('temperature')) is not None:
                generation_config['temperature'] = temperature
            if (top_p := model_settings.get('top_p')) is not None:
                generation_config['top_p'] = top_p
        if generation_config:
            request_data['generation_config'] = generation_config

        url = self.url + ('streamGenerateContent' if streamed else 'generateContent')

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': get_user_agent(),
            **await self.auth.headers(),
        }

        request_json = _gemini_request_ta.dump_json(request_data, by_alias=True)

        async with self.http_client.stream(
            'POST',
            url,
            content=request_json,
            headers=headers,
            timeout=(model_settings or {}).get('timeout', USE_CLIENT_DEFAULT),
        ) as r:
            if r.status_code != 200:
                await r.aread()
                raise exceptions.UnexpectedModelBehavior(f'Unexpected response from gemini {r.status_code}', r.text)
            yield r

    @staticmethod
    def _process_response(response: _GeminiResponse) -> ModelResponse:
        if len(response['candidates']) != 1:
            raise UnexpectedModelBehavior('Expected exactly one candidate in Gemini response')
        parts = response['candidates'][0]['content']['parts']
        return _process_response_from_parts(parts)

    @staticmethod
    async def _process_streamed_response(http_response: HTTPResponse) -> EitherStreamedResponse:
        """Process a streamed response, and prepare a streaming response to return."""
        aiter_bytes = http_response.aiter_bytes()
        start_response: _GeminiResponse | None = None
        content = bytearray()

        async for chunk in aiter_bytes:
            content.extend(chunk)
            responses = _gemini_streamed_response_ta.validate_json(
                content,
                experimental_allow_partial='trailing-strings',
            )
            if responses:
                last = responses[-1]
                if last['candidates'] and last['candidates'][0]['content']['parts']:
                    start_response = last
                    break

        if start_response is None:
            raise UnexpectedModelBehavior('Streamed response ended without content or tool calls')

        # TODO: Update this once we rework stream responses to be more flexible
        if _extract_response_parts(start_response).is_left():
            return GeminiStreamStructuredResponse(_content=content, _stream=aiter_bytes)
        else:
            return GeminiStreamTextResponse(_json_content=content, _stream=aiter_bytes)

    @staticmethod
    def _message_to_gemini_system_prompt(m: Message) -> _GeminiTextPart | None:
        """Convert a message to a _GeminiTextPart for "system_instructions" or _GeminiContent for "contents"."""
        if isinstance(m, SystemPrompt):
            return _GeminiTextPart(text=m.content)
        else:
            return None

    @staticmethod
    def _message_to_gemini_content(m: Message) -> _GeminiContent | None:
        if isinstance(m, SystemPrompt):
            return None
        elif isinstance(m, UserPrompt):
            return _content_user_prompt(m)
        elif isinstance(m, ToolReturn):
            return _content_tool_return(m)
        elif isinstance(m, RetryPrompt):
            return _content_retry_prompt(m)
        elif isinstance(m, ModelResponse):
            return _content_model_response(m)
        else:
            assert_never(m)


@dataclass
class GeminiStreamTextResponse(StreamTextResponse):
    """Implementation of `StreamTextResponse` for the Gemini model."""

    _json_content: bytearray
    _stream: AsyncIterator[bytes]
    _position: int = 0
    _timestamp: datetime = field(default_factory=_utils.now_utc, init=False)
    _cost: result.Cost = field(default_factory=result.Cost, init=False)

    async def __anext__(self) -> None:
        chunk = await self._stream.__anext__()
        self._json_content.extend(chunk)

    def get(self, *, final: bool = False) -> Iterable[str]:
        if final:
            all_items = pydantic_core.from_json(self._json_content)
            new_items = all_items[self._position :]
            self._position = len(all_items)
            new_responses = _gemini_streamed_response_ta.validate_python(new_items)
        else:
            all_items = pydantic_core.from_json(self._json_content, allow_partial=True)
            new_items = all_items[self._position : -1]
            self._position = len(all_items) - 1
            new_responses = _gemini_streamed_response_ta.validate_python(
                new_items, experimental_allow_partial='trailing-strings'
            )
        for r in new_responses:
            self._cost += _metadata_as_cost(r)
            parts = r['candidates'][0]['content']['parts']
            if _all_text_parts(parts):
                for part in parts:
                    yield part['text']
            else:
                raise UnexpectedModelBehavior(
                    'Streamed response with unexpected content, expected all parts to be text'
                )

    def cost(self) -> result.Cost:
        return self._cost

    def timestamp(self) -> datetime:
        return self._timestamp


@dataclass
class GeminiStreamStructuredResponse(StreamStructuredResponse):
    """Implementation of `StreamStructuredResponse` for the Gemini model."""

    _content: bytearray
    _stream: AsyncIterator[bytes]
    _timestamp: datetime = field(default_factory=_utils.now_utc, init=False)
    _cost: result.Cost = field(default_factory=result.Cost, init=False)

    async def __anext__(self) -> None:
        chunk = await self._stream.__anext__()
        self._content.extend(chunk)

    def get(self, *, final: bool = False) -> ModelResponse:
        """Get the `ModelResponse` at this point.

        NOTE: It's not clear how the stream of responses should be combined because Gemini seems to always
        reply with a single response, when returning a structured data.

        I'm therefore assuming that each part contains a complete tool call, and not trying to combine data from
        separate parts.
        """
        responses = _gemini_streamed_response_ta.validate_json(
            self._content,
            experimental_allow_partial='off' if final else 'trailing-strings',
        )
        combined_parts: list[_GeminiPartUnion] = []
        self._cost = result.Cost()
        for r in responses:
            self._cost += _metadata_as_cost(r)
            candidate = r['candidates'][0]
            combined_parts.extend(candidate['content']['parts'])
        return _process_response_from_parts(combined_parts, timestamp=self._timestamp)

    def cost(self) -> result.Cost:
        return self._cost

    def timestamp(self) -> datetime:
        return self._timestamp


# We use typed dicts to define the Gemini API response schema
# once Pydantic partial validation supports, dataclasses, we could revert to using them
# TypeAdapters take care of validation and serialization


@pydantic.with_config(pydantic.ConfigDict(defer_build=True))
class _GeminiRequest(TypedDict):
    """Schema for an API request to the Gemini API.

    See <https://ai.google.dev/api/generate-content#request-body> for API docs.
    """

    contents: list[_GeminiContent]
    tools: NotRequired[_GeminiTools]
    tool_config: NotRequired[_GeminiToolConfig]
    # we don't implement `generationConfig`, instead we use a named tool for the response
    system_instruction: NotRequired[_GeminiTextContent]
    """
    Developer generated system instructions, see
    <https://ai.google.dev/gemini-api/docs/system-instructions?lang=rest>
    """
    generation_config: NotRequired[_GeminiGenerationConfig]


class _GeminiGenerationConfig(TypedDict, total=False):
    """Schema for an API request to the Gemini API.

    Note there are many additional fields available that have not been added yet.

    See <https://ai.google.dev/api/generate-content#generationconfig> for API docs.
    """

    max_output_tokens: int
    temperature: float
    top_p: float


class _GeminiContent(TypedDict):
    role: Literal['user', 'model']
    parts: list[_GeminiPartUnion]


def _content_user_prompt(m: UserPrompt) -> _GeminiContent:
    return _GeminiContent(role='user', parts=[_GeminiTextPart(text=m.content)])


def _content_tool_return(m: ToolReturn) -> _GeminiContent:
    f_response = _response_part_from_response(m.tool_name, m.model_response_object())
    return _GeminiContent(role='user', parts=[f_response])


def _content_retry_prompt(m: RetryPrompt) -> _GeminiContent:
    if m.tool_name is None:
        part = _GeminiTextPart(text=m.model_response())
    else:
        response = {'call_error': m.model_response()}
        part = _response_part_from_response(m.tool_name, response)
    return _GeminiContent(role='user', parts=[part])


def _content_model_response(m: ModelResponse) -> _GeminiContent:
    parts: list[_GeminiPartUnion] = []
    for item in m.parts:
        if isinstance(item, ToolCallPart):
            parts.append(_function_call_part_from_call(item))
        elif isinstance(item, TextPart):
            parts.append(_GeminiTextPart(text=item.content))
        else:
            assert_never(item)
    return _GeminiContent(role='model', parts=parts)


class _GeminiTextPart(TypedDict):
    text: str


class _GeminiFunctionCallPart(TypedDict):
    function_call: Annotated[_GeminiFunctionCall, pydantic.Field(alias='functionCall')]


def _function_call_part_from_call(tool: ToolCallPart) -> _GeminiFunctionCallPart:
    assert isinstance(tool.args, ArgsDict), f'Expected ArgsObject, got {tool.args}'
    return _GeminiFunctionCallPart(function_call=_GeminiFunctionCall(name=tool.tool_name, args=tool.args.args_dict))


def _process_response_from_parts(parts: Sequence[_GeminiPartUnion], timestamp: datetime | None = None) -> ModelResponse:
    items: list[ModelResponsePart] = []
    for part in parts:
        if 'text' in part:
            items.append(TextPart(part['text']))
        elif 'function_call' in part:
            items.append(ToolCallPart.from_dict(part['function_call']['name'], part['function_call']['args']))
        elif 'function_response' in part:
            raise exceptions.UnexpectedModelBehavior(
                f'Unsupported response from Gemini, expected all parts to be function calls or text, got: {part!r}'
            )
    return ModelResponse(items, timestamp=timestamp or _utils.now_utc())


class _GeminiFunctionCall(TypedDict):
    """See <https://ai.google.dev/api/caching#FunctionCall>."""

    name: str
    args: dict[str, Any]


class _GeminiFunctionResponsePart(TypedDict):
    function_response: Annotated[_GeminiFunctionResponse, pydantic.Field(alias='functionResponse')]


def _response_part_from_response(name: str, response: dict[str, Any]) -> _GeminiFunctionResponsePart:
    return _GeminiFunctionResponsePart(function_response=_GeminiFunctionResponse(name=name, response=response))


class _GeminiFunctionResponse(TypedDict):
    """See <https://ai.google.dev/api/caching#FunctionResponse>."""

    name: str
    response: dict[str, Any]


def _part_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        if 'text' in v:
            return 'text'
        elif 'functionCall' in v or 'function_call' in v:
            return 'function_call'
        elif 'functionResponse' in v or 'function_response' in v:
            return 'function_response'
    return 'text'


# See <https://ai.google.dev/api/caching#Part>
# we don't currently support other part types
# TODO discriminator
_GeminiPartUnion = Annotated[
    Union[
        Annotated[_GeminiTextPart, pydantic.Tag('text')],
        Annotated[_GeminiFunctionCallPart, pydantic.Tag('function_call')],
        Annotated[_GeminiFunctionResponsePart, pydantic.Tag('function_response')],
    ],
    pydantic.Discriminator(_part_discriminator),
]


class _GeminiTextContent(TypedDict):
    role: Literal['user', 'model']
    parts: list[_GeminiTextPart]


class _GeminiTools(TypedDict):
    function_declarations: list[Annotated[_GeminiFunction, pydantic.Field(alias='functionDeclarations')]]


class _GeminiFunction(TypedDict):
    name: str
    description: str
    parameters: NotRequired[dict[str, Any]]
    """
    ObjectJsonSchema isn't really true since Gemini only accepts a subset of JSON Schema
    <https://ai.google.dev/gemini-api/docs/function-calling#function_declarations>
    and
    <https://ai.google.dev/api/caching#FunctionDeclaration>
    """


def _function_from_abstract_tool(tool: ToolDefinition) -> _GeminiFunction:
    json_schema = _GeminiJsonSchema(tool.parameters_json_schema).simplify()
    f = _GeminiFunction(
        name=tool.name,
        description=tool.description,
    )
    if json_schema.get('properties'):
        f['parameters'] = json_schema
    return f


class _GeminiToolConfig(TypedDict):
    function_calling_config: _GeminiFunctionCallingConfig


def _tool_config(function_names: list[str]) -> _GeminiToolConfig:
    return _GeminiToolConfig(
        function_calling_config=_GeminiFunctionCallingConfig(mode='ANY', allowed_function_names=function_names)
    )


class _GeminiFunctionCallingConfig(TypedDict):
    mode: Literal['ANY', 'AUTO']
    allowed_function_names: list[str]


@pydantic.with_config(pydantic.ConfigDict(defer_build=True))
class _GeminiResponse(TypedDict):
    """Schema for the response from the Gemini API.

    See <https://ai.google.dev/api/generate-content#v1beta.GenerateContentResponse>
    and <https://cloud.google.com/vertex-ai/docs/reference/rest/v1/GenerateContentResponse>
    """

    candidates: list[_GeminiCandidates]
    # usageMetadata appears to be required by both APIs but is omitted when streaming responses until the last response
    usage_metadata: NotRequired[Annotated[_GeminiUsageMetaData, pydantic.Field(alias='usageMetadata')]]
    prompt_feedback: NotRequired[Annotated[_GeminiPromptFeedback, pydantic.Field(alias='promptFeedback')]]


# TODO: Delete the next three functions once we've reworked streams to be more flexible
def _extract_response_parts(
    response: _GeminiResponse,
) -> _utils.Either[list[_GeminiFunctionCallPart], list[_GeminiTextPart]]:
    """Extract the parts of the response from the Gemini API.

    Returns Either a list of function calls (Either.left) or a list of text parts (Either.right).
    """
    if len(response['candidates']) != 1:
        raise UnexpectedModelBehavior('Expected exactly one candidate in Gemini response')
    parts = response['candidates'][0]['content']['parts']
    if _all_function_call_parts(parts):
        return _utils.Either(left=parts)
    elif _all_text_parts(parts):
        return _utils.Either(right=parts)
    else:
        raise exceptions.UnexpectedModelBehavior(
            f'Unsupported response from Gemini, expected all parts to be function calls or text, got: {parts!r}'
        )


def _all_function_call_parts(parts: list[_GeminiPartUnion]) -> TypeGuard[list[_GeminiFunctionCallPart]]:
    return all('function_call' in part for part in parts)


def _all_text_parts(parts: list[_GeminiPartUnion]) -> TypeGuard[list[_GeminiTextPart]]:
    return all('text' in part for part in parts)


class _GeminiCandidates(TypedDict):
    """See <https://ai.google.dev/api/generate-content#v1beta.Candidate>."""

    content: _GeminiContent
    finish_reason: NotRequired[Annotated[Literal['STOP', 'MAX_TOKENS'], pydantic.Field(alias='finishReason')]]
    """
    See <https://ai.google.dev/api/generate-content#FinishReason>, lots of other values are possible,
    but let's wait until we see them and know what they mean to add them here.
    """
    avg_log_probs: NotRequired[Annotated[float, pydantic.Field(alias='avgLogProbs')]]
    index: NotRequired[int]
    safety_ratings: NotRequired[Annotated[list[_GeminiSafetyRating], pydantic.Field(alias='safetyRatings')]]


class _GeminiUsageMetaData(TypedDict, total=False):
    """See <https://ai.google.dev/api/generate-content#FinishReason>.

    The docs suggest all fields are required, but some are actually not required, so we assume they are all optional.
    """

    prompt_token_count: Annotated[int, pydantic.Field(alias='promptTokenCount')]
    candidates_token_count: NotRequired[Annotated[int, pydantic.Field(alias='candidatesTokenCount')]]
    total_token_count: Annotated[int, pydantic.Field(alias='totalTokenCount')]
    cached_content_token_count: NotRequired[Annotated[int, pydantic.Field(alias='cachedContentTokenCount')]]


def _metadata_as_cost(response: _GeminiResponse) -> result.Cost:
    metadata = response.get('usage_metadata')
    if metadata is None:
        return result.Cost()
    details: dict[str, int] = {}
    if cached_content_token_count := metadata.get('cached_content_token_count'):
        details['cached_content_token_count'] = cached_content_token_count
    return result.Cost(
        request_tokens=metadata.get('prompt_token_count', 0),
        response_tokens=metadata.get('candidates_token_count', 0),
        total_tokens=metadata.get('total_token_count', 0),
        details=details,
    )


class _GeminiSafetyRating(TypedDict):
    """See <https://ai.google.dev/gemini-api/docs/safety-settings#safety-filters>."""

    category: Literal[
        'HARM_CATEGORY_HARASSMENT',
        'HARM_CATEGORY_HATE_SPEECH',
        'HARM_CATEGORY_SEXUALLY_EXPLICIT',
        'HARM_CATEGORY_DANGEROUS_CONTENT',
        'HARM_CATEGORY_CIVIC_INTEGRITY',
    ]
    probability: Literal['NEGLIGIBLE', 'LOW', 'MEDIUM', 'HIGH']


class _GeminiPromptFeedback(TypedDict):
    """See <https://ai.google.dev/api/generate-content#v1beta.GenerateContentResponse>."""

    block_reason: Annotated[str, pydantic.Field(alias='blockReason')]
    safety_ratings: Annotated[list[_GeminiSafetyRating], pydantic.Field(alias='safetyRatings')]


_gemini_request_ta = pydantic.TypeAdapter(_GeminiRequest)
_gemini_response_ta = pydantic.TypeAdapter(_GeminiResponse)

# steam requests return a list of https://ai.google.dev/api/generate-content#method:-models.streamgeneratecontent
_gemini_streamed_response_ta = pydantic.TypeAdapter(list[_GeminiResponse], config=pydantic.ConfigDict(defer_build=True))


class _GeminiJsonSchema:
    """Transforms the JSON Schema from Pydantic to be suitable for Gemini.

    Gemini which [supports](https://ai.google.dev/gemini-api/docs/function-calling#function_declarations)
    a subset of OpenAPI v3.0.3.

    Specifically:
    * gemini doesn't allow the `title` keyword to be set
    * gemini doesn't allow `$defs` — we need to inline the definitions where possible
    """

    def __init__(self, schema: _utils.ObjectJsonSchema):
        self.schema = deepcopy(schema)
        self.defs = self.schema.pop('$defs', {})

    def simplify(self) -> dict[str, Any]:
        self._simplify(self.schema, refs_stack=())
        return self.schema

    def _simplify(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        schema.pop('title', None)
        schema.pop('default', None)
        if ref := schema.pop('$ref', None):
            # noinspection PyTypeChecker
            key = re.sub(r'^#/\$defs/', '', ref)
            if key in refs_stack:
                raise exceptions.UserError('Recursive `$ref`s in JSON Schema are not supported by Gemini')
            refs_stack += (key,)
            schema_def = self.defs[key]
            self._simplify(schema_def, refs_stack)
            schema.update(schema_def)
            return

        if any_of := schema.get('anyOf'):
            for schema in any_of:
                self._simplify(schema, refs_stack)

        type_ = schema.get('type')

        if type_ == 'object':
            self._object(schema, refs_stack)
        elif type_ == 'array':
            return self._array(schema, refs_stack)

    def _object(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        ad_props = schema.pop('additionalProperties', None)
        if ad_props:
            raise exceptions.UserError('Additional properties in JSON Schema are not supported by Gemini')

        if properties := schema.get('properties'):  # pragma: no branch
            for value in properties.values():
                self._simplify(value, refs_stack)

    def _array(self, schema: dict[str, Any], refs_stack: tuple[str, ...]) -> None:
        if prefix_items := schema.get('prefixItems'):
            # TODO I think this not is supported by Gemini, maybe we should raise an error?
            for prefix_item in prefix_items:
                self._simplify(prefix_item, refs_stack)

        if items_schema := schema.get('items'):  # pragma: no branch
            self._simplify(items_schema, refs_stack)