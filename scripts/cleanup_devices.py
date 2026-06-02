from sqlmodel import Session, select
from app.database import engine
from app.users.models import UserDevice
# Import all models to resolve forward references
from app.users.models import UserDevice, User
from app.messages.models import Chat, Message
from app.posts.models import Post, Comment
from app.meme_templates.models import MemeTemplates

def cleanup_devices():
    with Session(engine) as session:
        # Find bad devices
        statement = select(UserDevice).where(
            (UserDevice.device_id == None) | (UserDevice.public_key == None)
        )
        bad_devices = session.exec(statement).all()
        count = len(bad_devices)
        
        if count > 0:
            print(f"Found {count} corrupted device references. Deleting...")
            for d in bad_devices:
                session.delete(d)
            session.commit()
            print("Cleanup complete.")
        else:
            print("No corrupted devices found.")

if __name__ == "__main__":
    cleanup_devices()
