BACKEND_HOST: str = "skunkk.xyz"
FRONTEND_HOST: str = "api.skunkk.xyz"
WARP: str = "socks5://127.0.0.1:7483"
FERNET_KEY: str = "0GKftpvX45aoHDZ1p4_OgYuaoPnI2TEPnJGeuvPjXjg="
PIPED_API: str = "pipedapi.adminforge.de"
PUBSUB_KEY: str = "qXhjfaXxt2e_2WrkWwx3QR"
IPC_KEY: str = "ZguzxNhhtz4PG6zOaD0"

class Colors:
    greed = 0x829dff
    approve = 0x71aa51
    deny = 0xFF0000
    warning = 0xff3835
    info = 0x0000FF
    neutral = 0xFFFFFF

class DISCORD:
    TOKEN: str = (
        ""
    )
    PUBLIC_KEY: str = "106c561c9e3de75150aa63a7f7cf28ab136ce6bae16a2b5cd2f0afec72af52b9"
    CLIENT_ID: str = "1237397616845914205"
    CLIENT_SECRET: str = "SHtdaACxV1D1ryMwweo_h7lmcCZoebTZ"
    REDIRECT_URI: str = "https://x.X.sh/login"


class CLIENT:
    PREFIX: str = ";"
    DESCRIPTION: str | None = None
    OWNER_IDS: list[int] = [1247076592556183598]
    SUPPORT_URL: str = "https://discord.gg/UwhUBDz6Js"


class LAVALINK:
    NODE_COUNT: int = 1
    HOST: str = "0.0.0.0"
    PORT: int = 2333
    PASSWORD: str = "3o8RHRo0or0aMyCLf0HBHTpfgjcQ1S8zpOgnMMhhF7FACSNJpH"


class NETWORK:
    HOST: str = "0.0.0.0"
    PORT: int = 8759


class DATABASE:
    DSN: str = "postgres://postgres:admin@127.0.0.1/greed"


class REDIS:
    DB: int = 0
    HOST: str = "localhost"
    PORT: int = 6379


class Authorization:
    FNBR: str = ""
    CLEVER: str = ""
    WOLFRAM: str = ""
    WEATHER: str = ""
    OSU: str = ""
    LASTFM: list[str] = [
        "",
        "",
    ]
    SOUNDCLOUD: str = ""
    GEMINI: str = ""
    JEYY: str = ""
    KRAKEN: str = ""

    class GOOGLE:
        CX: str = ""
        KEY: str = ""

    class TWITCH:
        CLIENT_ID: str = ""
        CLIENT_SECRET: str = ""

    class SPOTIFY:
        CLIENT_ID: str = ""
        CLIENT_SECRET: str = ""

    class REDDIT:
        CLIENT_ID: str = ""
        CLIENT_SECRET: str = ""

    class INSTAGRAM:
        COOKIES: list[dict] = [
            {
                "ds_user_id": "5719713909",
                "sessionid": "5719713909%3AZ5qcNnZV2FSJjj%3A24%3AAYcI8eInCXXXCisD_sxi9SHb2P3h9B0od_qd0VWxQA",
            },
        ]
        GRAPHQL: list[str] = [
            'mid=Zk6-2QALAAFtMbToQ2XjSSBYlsiA; ig_did=757FA0A5-7DAA-4117-8D6B-B9142A62BC99; ig_nrcb=1; datr=2L5OZiH1rWO0qIj1TVPLiQEo; ps_n=1; ps_l=1; shbid="2800\05460028618709\0541748486899:01f7a1810b46cd3a968194a84600d7d2de1969bbc6e1df09befdaf8eaf94d6f0ffa61794"; shbts="1716950899\05460028618709\0541748486899:01f792c601b0b93cdb9969d1a8d3b47d5a8efd2c58b1f55083894f0da9d53863ab2ad7f1"; igd_ls=%7B%2217842319535002710%22%3A%7B%22c%22%3A%7B%221%22%3A%22HCwAABaUshEWwPq3tQMTBRas0dLd19-xPwA%22%7D%2C%22d%22%3A%22bb054b9a-9bc8-4514-941b-212df1b60810%22%2C%22s%22%3A%220%22%2C%22u%22%3A%22g9vo1t%22%7D%7D; wd=1279x991; csrftoken=i92fz69IPpIoNUIPEC6FVczDvJ3hAcM1; ds_user_id=5719713909; sessionid=5719713909%3AZ5qcNnZV2FSJjj%3A24%3AAYcI8eInCXXXCisD_sxi9SHb2P3h9B0od_qd0VWxQA; rur="FRC\0545719713909\0541748740973:01f7880367a9a49fcaa0f973f546178da8295f1fecf2a67fa4f136906cc2aafbd2cdfeae"'
        ]


class EMOJIS:
    class BADGES:
        HYPESQUAD_BRILLIANCE: str = "<:hypesquad_brilliance:1243011291271135254>"
        BOOST: str = "<:boost:1243011291963068476>"
        STAFF: str = "<:staff:1243011292667580426>"
        VERIFIED_BOT_DEVELOPER: str = "<:verified_bot_developer:1243011293569613834>"
        SERVER_OWNER: str = "<:server_owner:1243011294391566417>"
        HYPESQUAD_BRAVERY: str = "<:hypesquad_bravery:1243011295016390689>"
        PARTNER: str = "<:partner:1243011296392253510>"
        HYPESQUAD_BALANCE: str = "<:hypesquad_balance:1243011297738625104>"
        EARLY_SUPPORTER: str = "<:early_supporter:1243011298459914261>"
        HYPESQUAD: str = "<:hypesquad:1243011299323936798>"
        BUG_HUNTER_LEVEL_2: str = "<:bug_hunter_level_2:1243011300716707882>"
        CERTIFIED_MODERATOR: str = "<:certified_moderator:1243011301568024597>"
        NITRO: str = "<:nitro:1243011302192975933>"
        BUG_HUNTER: str = "<:bug_hunter:1243011303384285225>"
        ACTIVE_DEVELOPER: str = "<:active_developer:1243011303988269148>"

    class PAGINATOR:
        NEXT: str = "<:next:1243011305053622343>"
        NAVIGATE: str = "<:navigate:1243011305770586204>"
        PREVIOUS: str = "<:previous:1243011306450059264>"
        CANCEL: str = "<:cancel:1243011306932404356>"

    class AUDIO:
        SKIP: str = "<:skip:1243011308333564006>"
        RESUME: str = "<:resume:1243011309449252864>"
        REPEAT: str = "<:repeat:1243011309843382285>"
        PREVIOUS: str = "<:previous:1243011310942162990>"
        PAUSE: str = "<:pause:1243011311860842627>"
        QUEUE: str = "<:queue:1243011313006022698>"
        REPEAT_TRACK: str = "<:repeat_track:1243011313660334101>"
    
    class Embed:
        AN_ON: str = "🟢"
        AN_OFF: str = "🔴"
