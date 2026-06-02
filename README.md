# Meeloop Backend

A comprehensive social media backend API built with FastAPI, featuring real-time messaging, video calls, stories, and anonymous interactions through the "Loop" feature.

## 🚀 Features

### Core Social Media Features
- **Posts & Media** - Create posts with multiple media attachments (images, videos, files)
- **Like & Comment System** - Interactive engagement with posts
- **Bookmark System** - Save posts in organized folders
- **User Following** - Follow/unfollow other users
- **Stories** - 24-hour expiring content with media support
- **Feed Algorithm** - Personalized content feed based on following

### Real-time Communication
- **Instant Messaging** - WebSocket-based real-time chat
- **Voice & Video Calls** - WebRTC integration for calls
- **Message Reactions** - Emoji reactions on messages
- **Message Status** - Read receipts and delivery status
- **File Sharing** - Share various file types in conversations

### Loop Feature (18+)
- **Anonymous Profiles** - Separate anonymous identity system
- **Friend Discovery** - Find and connect with nearby users
- **Random Chat** - Meet new people through random matching
- **Dedicated Messaging** - Separate chat system for Loop connections

### Additional Features
- **Contact Management** - Sync and manage phone contacts
- **Push Notifications** - Firebase Cloud Messaging integration
- **Call History** - Track voice and video call records
- **Meme Templates** - Manage and share meme templates
- **SEO Optimization** - Social media sharing optimization

## 🛠️ Technology Stack

- **Framework**: FastAPI
- **Database**: SQLite with SQLModel
- **Authentication**: JWT with OAuth2
- **Real-time**: Socket.IO
- **File Storage**: Local filesystem
- **Notifications**: Firebase Admin SDK
- **Validation**: Pydantic
- **Migrations**: Alembic

## 📋 Prerequisites

- Python 3.8+
- pip (Python package manager)

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd meeloop-backend
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv env
   source env/bin/activate  # On Windows: env\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   Create a `.env` file in the root directory:
   ```env
   SECRET_KEY=your-secret-key-here
   DATABASE_URL=sqlite:///./database.sqlite
   ```

5. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

6. **Start the development server**
   ```bash
   uvicorn main:app --reload
   ```

The API will be available at `http://localhost:8000`

## 📚 API Documentation

Once the server is running, you can access:
- **Interactive API docs**: `http://localhost:8000/docs`
- **ReDoc documentation**: `http://localhost:8000/redoc`

## 🏗️ Project Structure

```
backend/
├── app/
│   ├── users/              # User management & authentication
│   ├── posts/              # Social media posts & media
│   ├── messages/           # Real-time messaging system
│   ├── contacts/           # Contact management
│   ├── stories/            # 24-hour story feature
│   ├── calls/              # Voice/video call management
│   ├── notifications/      # Push notification system
│   ├── loops/              # Anonymous interaction feature
│   ├── meme_templates/     # Meme template management
│   ├── sockets/            # WebSocket & Socket.IO events
│   ├── database.py         # Database configuration
│   ├── dependencies.py     # FastAPI dependencies
│   └── security.py         # Authentication & security
├── media/                  # Static media files
├── migrations/             # Database migrations
├── main.py                 # Application entry point
└── requirements.txt        # Python dependencies
```

## 🔐 Authentication

The API uses JWT-based authentication with OAuth2 password flow:

1. **Register**: `POST /signup/`
2. **Login**: `POST /login/` or `POST /login-user/`
3. **Access Token**: Use the returned token in the `Authorization` header:
   ```
   Authorization: Bearer <your-jwt-token>
   ```

## 📱 Key API Endpoints

### Authentication
- `POST /signup/` - User registration
- `POST /login/` - User login
- `GET /me/` - Get current user profile

### Posts
- `GET /feed` - Get personalized feed
- `POST /posts/` - Create new post
- `POST /posts/{id}/like/` - Like/unlike post
- `GET /comments/{id}/` - Get post comments
- `POST /comments/{id}/` - Add comment

### Messaging
- `GET /messages/{receiver_id}/` - Get conversation
- `POST /messages/{receiver_id}/` - Send message
- `GET /chats/` - Get user chats
- `DELETE /messages/{id}` - Delete message

### Stories
- `POST /story/upload` - Upload story
- `GET /story/` - Get stories from followed users
- `DELETE /story/{id}` - Delete story

### Loop Features
- `GET /loops/nearby` - Discover nearby users
- `POST /loops/request` - Send friend request
- `GET /loops/friends` - Get Loop friends
- `POST /loops/chat/{id}/message` - Send Loop message

## 🔌 WebSocket Events

Connect to `ws://localhost:8000/ws/socket.io/` with your JWT token:

### Message Events
- `message:send` - Send real-time message
- `message:edit` - Edit message
- `message:delete` - Delete message
- `reaction:add` - Add emoji reaction
- `reaction:remove` - Remove emoji reaction

### Call Events
- `call_offer` - Initiate voice/video call
- `call_answer` - Answer call
- `call_end` - End call

## 📊 Database Schema

### Key Models
- **User** - User accounts and profiles
- **Post** - Social media posts with media
- **Message** - Real-time chat messages
- **Chat** - Conversation management
- **Story** - Temporary 24-hour content
- **Call** - Voice/video call records
- **LoopProfile** - Anonymous user profiles
- **Notification** - Push notifications
- **Contact** - User contact management

## 🔧 Configuration

### Environment Variables
```env
SECRET_KEY=your-jwt-secret-key
DATABASE_URL=sqlite:///./database.sqlite
FIREBASE_CREDENTIALS=path/to/firebase-credentials.json
```

### Database
The application uses SQLite by default. For production, consider PostgreSQL:
```env
DATABASE_URL=postgresql://user:password@localhost/meeloop
```
---

**Meeloop Backend** - Building the future of social media interactions 🚀
