from aiohttp.web import (
    Application,
    RouteTableDef,
    Request,
    Response,
    run_app,
    json_response,
)
import config
from discord.ext.ipc.client import Client  # type: ignore
import xmltodict  # type: ignore

routes = RouteTableDef()
client = Client(secret_key=config.IPC_KEY)


@routes.get("/features.json")
async def features(request: Request):
    data = await client.request("features")
    if not data or not data.response:
        return Response(status=500)

    return json_response(data.response)


@routes.get("/tree.txt")
async def tree(request: Request):
    data = await client.request("tree")
    if not data or not data.response:
        return Response(status=500)

    return Response(text=data.response["tree"], content_type="text/plain")  # type: ignore


@routes.get(f"/pubsub/{config.PUBSUB_KEY}")
async def pubsub_receiver(request: Request):
    topic = request.query.get("hub.topic")
    lease = request.query.get("hub.lease_seconds")

    if topic and lease:
        channel_id = topic.split("channel_id=")[-1]
        await client.request("subscribe", lease=lease, channel_id=channel_id)

    challenge = request.query.get("hub.challenge")
    return Response(text=challenge or "ok")


@routes.post(f"/pubsub/{config.PUBSUB_KEY}")
async def pubsub_publisher(request: Request):
    body = await request.text()
    data = xmltodict.parse(body)["feed"]
    if not (entry := data.get("entry")):
        return Response(text="no video", status=204)

    video_id, channel_id = entry["yt:videoId"], entry["yt:channelId"]
    data = await client.request(
        "pubsub",
        video_id=video_id,
        channel_id=channel_id,
        published=entry["published"],
    )
    if not data or not data.response:
        return Response(status=500)

    return Response(text=data.response["text"], status=204)  # type: ignore


app = Application()
app.add_routes(routes)
run_app(
    app,
    host=config.NETWORK.HOST,
    port=config.NETWORK.PORT,
    reuse_port=True,
    print=lambda _: _,
)
