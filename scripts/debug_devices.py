from sqlmodel import Session, select
from app.database import engine
# Import all models to resolve forward references
from app.users.models import UserDevice, User
from app.messages.models import Chat, Message
from app.posts.models import Post, Comment
from app.meme_templates.models import MemeTemplates

def inspect_devices():
    with Session(engine) as session:
        devices = session.exec(select(UserDevice)).all()
        print(f"Total Devices: {len(devices)}")
        for d in devices:
            user = session.get(User, d.user_id)
            username = user.username if user else "UNKNOWN"
            print(f"ID: {d.id} | User: {username} ({d.user_id}) | DeviceID: {d.device_id} | Active: {d.is_active} | PubKey: {d.public_key[:20] if d.public_key else 'None'}...")

if __name__ == "__main__":
    inspect_devices()
