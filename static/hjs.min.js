const express = require('express');
const http = require('http');
const socketIo = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIo(server);

let adminSocket = null;
const viewers = new Map();

io.on('connection', (socket) => {
    console.log(`New connection: ${socket.id}`);

    socket.on('admin-ready', () => {
        console.log(`Admin ready: ${socket.id}`);
        adminSocket = socket;
        socket.isAdmin = true;
        
        // Notify any waiting viewers
        viewers.forEach((viewerSocket, viewerId) => {
            if (viewerSocket.waitingForBroadcast) {
                console.log(`Notifying waiting viewer: ${viewerId}`);
                socket.emit('new-viewer', viewerId);
                viewerSocket.waitingForBroadcast = false;
            }
        });
    });

    socket.on('viewer-ready', () => {
        console.log(`Viewer ready: ${socket.id}`);
        viewers.set(socket.id, socket);
        socket.isViewer = true;
        
        if (!adminSocket) {
            console.log('No admin available');
            socket.emit('no-broadcast');
            socket.waitingForBroadcast = true;
        } else {
            console.log(`Connecting viewer to admin: ${socket.id}`);
            adminSocket.emit('new-viewer', socket.id);
        }
    });

    // Relay WebRTC signaling
    socket.on('offer', (data) => {
        const target = viewers.get(data.target);
        if (target) {
            data.from = socket.id;
            target.emit('offer', data);
        }
    });

    socket.on('answer', (data) => {
        if (adminSocket && data.target === adminSocket.id) {
            data.from = socket.id;
            adminSocket.emit('answer', data);
        }
    });

    socket.on('ice-candidate', (data) => {
        const target = data.target === adminSocket?.id ? adminSocket : viewers.get(data.target);
        if (target) {
            data.from = socket.id;
            target.emit('ice-candidate', data);
        }
    });

    socket.on('broadcast-ended', () => {
        viewers.forEach((viewerSocket) => {
            viewerSocket.emit('broadcast-ended');
            viewerSocket.waitingForBroadcast = true;
        });
    });

    socket.on('disconnect', () => {
        console.log(`Disconnected: ${socket.id}`);
        
        if (socket.isAdmin) {
            adminSocket = null;
            viewers.forEach((viewerSocket) => {
                viewerSocket.emit('broadcast-ended');
            });
        } else if (socket.isViewer) {
            viewers.delete(socket.id);
            if (adminSocket) {
                adminSocket.emit('viewer-disconnected', socket.id);
            }
        }
    });
});

server.listen(3000, () => {
    console.log('Server running on port 3000');
});
