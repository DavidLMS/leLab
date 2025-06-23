# WebRTC Signaling Server for Unified Camera System

import json
import logging
import asyncio
from typing import Dict, Set, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
import uuid

logger = logging.getLogger(__name__)

class WebRTCSignalingServer:
    """WebRTC signaling server for camera streaming"""
    
    def __init__(self):
        # Active WebSocket connections
        self.connections: Dict[str, WebSocket] = {}
        
        # Camera sources registry
        self.camera_sources: Dict[str, Dict[str, Any]] = {}
        
        # Peer-to-peer mappings (for future remote cameras)
        self.peer_mappings: Dict[str, str] = {}
        
        # Connection metadata
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
    
    async def connect(self, websocket: WebSocket) -> str:
        """Accept a new WebSocket connection and return connection ID"""
        await websocket.accept()
        connection_id = str(uuid.uuid4())
        
        self.connections[connection_id] = websocket
        self.connection_metadata[connection_id] = {
            "connected_at": asyncio.get_event_loop().time(),
            "camera_sources": [],
            "peer_id": None
        }
        
        logger.info(f"🔗 WebRTC client connected: {connection_id}")
        logger.info(f"📊 Total connections: {len(self.connections)}")
        
        return connection_id
    
    def disconnect(self, connection_id: str):
        """Handle client disconnection"""
        if connection_id in self.connections:
            # Clean up camera sources for this connection
            metadata = self.connection_metadata.get(connection_id, {})
            camera_sources = metadata.get("camera_sources", [])
            
            for source_id in camera_sources:
                if source_id in self.camera_sources:
                    logger.info(f"🗑️ Removing camera source: {source_id}")
                    del self.camera_sources[source_id]
            
            # Remove connection
            del self.connections[connection_id]
            if connection_id in self.connection_metadata:
                del self.connection_metadata[connection_id]
            
            logger.info(f"🔌 WebRTC client disconnected: {connection_id}")
            logger.info(f"📊 Total connections: {len(self.connections)}")
    
    async def handle_message(self, connection_id: str, message: Dict[str, Any]):
        """Handle incoming signaling message"""
        try:
            message_type = message.get("type")
            source_id = message.get("sourceId")
            
            logger.info(f"📨 Received {message_type} for source {source_id} from {connection_id}")
            
            if message_type == "register-camera":
                await self._handle_register_camera(connection_id, message)
            elif message_type == "offer":
                await self._handle_offer(connection_id, message)
            elif message_type == "answer":
                await self._handle_answer(connection_id, message)
            elif message_type == "ice-candidate":
                await self._handle_ice_candidate(connection_id, message)
            elif message_type == "request-camera-list":
                await self._handle_camera_list_request(connection_id)
            elif message_type == "request-camera-stream":
                await self._handle_camera_stream_request(connection_id, message)
            else:
                logger.warning(f"⚠️ Unknown message type: {message_type}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message from {connection_id}: {e}")
            await self._send_error(connection_id, str(e))
    
    async def _handle_register_camera(self, connection_id: str, message: Dict[str, Any]):
        """Register a new camera source"""
        source_id = message.get("sourceId")
        payload = message.get("payload", {})
        
        camera_info = {
            "id": source_id,
            "name": payload.get("name", "Unknown Camera"),
            "type": payload.get("type", "local"),
            "width": payload.get("width", 640),
            "height": payload.get("height", 480),
            "fps": payload.get("fps", 30),
            "owner_connection": connection_id,
            "status": "available",
            "registered_at": asyncio.get_event_loop().time()
        }
        
        self.camera_sources[source_id] = camera_info
        
        # Add to connection metadata
        if connection_id in self.connection_metadata:
            self.connection_metadata[connection_id]["camera_sources"].append(source_id)
        
        logger.info(f"📹 Registered camera: {camera_info['name']} ({source_id})")
        
        # Broadcast camera list update to all connections
        await self._broadcast_camera_list()
        
        # Confirm registration
        await self._send_message(connection_id, {
            "type": "camera-registered",
            "sourceId": source_id,
            "payload": {"status": "success"},
            "timestamp": int(asyncio.get_event_loop().time() * 1000)
        })
    
    async def _handle_offer(self, connection_id: str, message: Dict[str, Any]):
        """Handle WebRTC offer"""
        source_id = message.get("sourceId")
        target_id = message.get("targetId")
        
        if target_id and target_id in self.connections:
            # Forward offer to target connection (for remote cameras)
            await self._send_message(target_id, message)
        else:
            # For local cameras, this might be an offer to the server
            # We can handle it here or forward to all other connections
            logger.info(f"📤 Broadcasting offer for camera {source_id}")
            await self._broadcast_message(message, exclude=connection_id)
    
    async def _handle_answer(self, connection_id: str, message: Dict[str, Any]):
        """Handle WebRTC answer"""
        source_id = message.get("sourceId")
        target_id = message.get("targetId")
        
        if target_id and target_id in self.connections:
            # Forward answer to target connection
            await self._send_message(target_id, message)
        else:
            # Broadcast answer
            await self._broadcast_message(message, exclude=connection_id)
    
    async def _handle_ice_candidate(self, connection_id: str, message: Dict[str, Any]):
        """Handle ICE candidate"""
        source_id = message.get("sourceId")
        target_id = message.get("targetId")
        
        if target_id and target_id in self.connections:
            # Forward ICE candidate to target connection
            await self._send_message(target_id, message)
        else:
            # Broadcast ICE candidate
            await self._broadcast_message(message, exclude=connection_id)
    
    async def _handle_camera_list_request(self, connection_id: str):
        """Send available camera list to requesting connection"""
        camera_list = []
        for source_id, camera_info in self.camera_sources.items():
            camera_list.append({
                "id": source_id,
                "name": camera_info["name"],
                "type": camera_info["type"],
                "available": camera_info["status"] == "available",
                "width": camera_info["width"],
                "height": camera_info["height"],
                "fps": camera_info["fps"]
            })
        
        await self._send_message(connection_id, {
            "type": "camera-list",
            "sourceId": "server",
            "payload": {"cameras": camera_list},
            "timestamp": int(asyncio.get_event_loop().time() * 1000)
        })
    
    async def _handle_camera_stream_request(self, connection_id: str, message: Dict[str, Any]):
        """Handle request for camera stream"""
        requested_source_id = message.get("payload", {}).get("sourceId")
        
        if requested_source_id in self.camera_sources:
            camera_info = self.camera_sources[requested_source_id]
            owner_connection = camera_info["owner_connection"]
            
            if owner_connection in self.connections:
                # Forward stream request to camera owner
                stream_request = {
                    "type": "stream-request",
                    "sourceId": requested_source_id,
                    "targetId": connection_id,
                    "payload": {"requesterId": connection_id},
                    "timestamp": int(asyncio.get_event_loop().time() * 1000)
                }
                await self._send_message(owner_connection, stream_request)
            else:
                await self._send_error(connection_id, f"Camera owner not connected")
        else:
            await self._send_error(connection_id, f"Camera {requested_source_id} not found")
    
    async def _broadcast_camera_list(self):
        """Broadcast updated camera list to all connections"""
        camera_list = []
        for source_id, camera_info in self.camera_sources.items():
            camera_list.append({
                "id": source_id,
                "name": camera_info["name"],
                "type": camera_info["type"],
                "available": camera_info["status"] == "available"
            })
        
        broadcast_message = {
            "type": "camera-list-update",
            "sourceId": "server",
            "payload": {"cameras": camera_list},
            "timestamp": int(asyncio.get_event_loop().time() * 1000)
        }
        
        await self._broadcast_message(broadcast_message)
    
    async def _send_message(self, connection_id: str, message: Dict[str, Any]):
        """Send message to specific connection"""
        if connection_id in self.connections:
            try:
                websocket = self.connections[connection_id]
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"❌ Failed to send message to {connection_id}: {e}")
                # Remove dead connection
                self.disconnect(connection_id)
    
    async def _broadcast_message(self, message: Dict[str, Any], exclude: Optional[str] = None):
        """Broadcast message to all connections except excluded one"""
        dead_connections = []
        
        for connection_id, websocket in self.connections.items():
            if connection_id == exclude:
                continue
                
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"❌ Failed to broadcast to {connection_id}: {e}")
                dead_connections.append(connection_id)
        
        # Clean up dead connections
        for dead_id in dead_connections:
            self.disconnect(dead_id)
    
    async def _send_error(self, connection_id: str, error_message: str):
        """Send error message to connection"""
        error_msg = {
            "type": "error",
            "sourceId": "server",
            "payload": {"error": error_message},
            "timestamp": int(asyncio.get_event_loop().time() * 1000)
        }
        await self._send_message(connection_id, error_msg)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get signaling server statistics"""
        return {
            "total_connections": len(self.connections),
            "total_cameras": len(self.camera_sources),
            "local_cameras": len([c for c in self.camera_sources.values() if c["type"] == "local"]),
            "remote_cameras": len([c for c in self.camera_sources.values() if c["type"] == "remote"]),
            "active_cameras": len([c for c in self.camera_sources.values() if c["status"] == "available"])
        }

# Global signaling server instance
signaling_server = WebRTCSignalingServer()