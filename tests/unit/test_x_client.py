import pytest
import respx
from httpx import Response
from safe_monitor.sources.x_client import TwitterApiIoClient


@pytest.mark.asyncio
async def test_resolve_handle_returns_user_id():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://api.twitterapi.io/twitter/user/info",
            params={"userName": "samczsun"},
        ).mock(return_value=Response(200, json={"data": {"id": "12345", "userName": "samczsun"}}))
        uid = await client.resolve_handle("samczsun")
    assert uid == "12345"


@pytest.mark.asyncio
async def test_resolve_handle_unknown_returns_none():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://api.twitterapi.io/twitter/user/info",
            params={"userName": "nope"},
        ).mock(return_value=Response(404, json={"error": "not found"}))
        uid = await client.resolve_handle("nope")
    assert uid is None


@pytest.mark.asyncio
async def test_get_last_tweets_returns_list_and_filters_since_id():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    payload = {"tweets": [
        {"id_str": "100", "text": "old"},
        {"id_str": "200", "text": "new"},
    ]}
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets").mock(
            return_value=Response(200, json=payload)
        )
        out = await client.get_last_tweets(user_id="42", since_id="150")
    assert [t["id_str"] for t in out] == ["200"]


@pytest.mark.asyncio
async def test_get_last_tweets_handles_live_nested_shape():
    # Live TwitterAPI.io wraps tweets inside data["data"]["tweets"];
    # earlier code fed the inner dict to a list iteration.
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    payload = {
        "status": "success",
        "data": {
            "pin_tweet": None,
            "tweets": [
                {"id": "2052889662805713248", "text": "live shape", "author": {"userName": "x"}}
            ],
        },
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets").mock(
            return_value=Response(200, json=payload)
        )
        out = await client.get_last_tweets(user_id="42")
    assert [t["id"] for t in out] == ["2052889662805713248"]


@pytest.mark.asyncio
async def test_402_raises_credits_exhausted():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/info").mock(
            return_value=Response(402, text="payment required")
        )
        with pytest.raises(TwitterApiIoClient.CreditsExhausted):
            await client.resolve_handle("anyone")
