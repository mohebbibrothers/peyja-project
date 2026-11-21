"""تست‌های طبقه‌بندی رویداد وب‌هوک — echo ≠ ورودی."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from peyja.loop.dispatcher import ClassifiedEvent, classify_event, record_inbound
from peyja.models import Base


def test_classify_inbound_message():
    payload = {
        "event_type": "messaging",
        "account_id": "acc-1",
        "data": {
            "sender": {"id": "user-9"},
            "recipient": {"id": "page-1"},
            "message": {"mid": "m1", "text": "قیمت چنده؟"},
            "timestamp": 1763726400000,
        },
    }
    ev = classify_event(payload)
    assert ev.kind == "inbound_message"
    assert ev.ig_user_id == "user-9"
    assert ev.text == "قیمت چنده؟"
    assert ev.mid == "m1"


def test_classify_echo_message():
    payload = {
        "event_type": "messaging",
        "account_id": "acc-1",
        "data": {
            "sender": {"id": "page-1"},
            "recipient": {"id": "user-9"},
            "message": {"mid": "m2", "text": "قیمت ۴/۸۰", "is_echo": True},
        },
    }
    ev = classify_event(payload)
    assert ev.kind == "echo_message"


def test_classify_action_result():
    ev = classify_event({"event_type": "action.send_message", "account_id": "acc-1", "data": {}})
    assert ev.kind == "action_result"


def test_classify_comment_and_unknown():
    assert classify_event({"event_type": "comments", "data": {}}).kind == "comment"
    assert classify_event({"event_type": "weird", "data": {}}).kind == "unknown"


def test_record_inbound_opens_conversation():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            ev = ClassifiedEvent(
                kind="inbound_message",
                account_id="acc-1",
                ig_user_id="user-9",
                text="سلام",
                mid="m1",
            )
            await record_inbound(session, ev)
            await record_inbound(session, ev)  # تکراری — نباید گفتگوی دوم بسازد
            from peyja.models import Conversation, Message

            convs = (await session.execute(Conversation.__table__.select())).all()
            msgs = (await session.execute(Message.__table__.select())).all()
            assert len(convs) == 1
            assert len(msgs) == 2
            assert convs[0].window_expires_at is not None
            await session.commit()
        await engine.dispose()

    asyncio.run(run())
