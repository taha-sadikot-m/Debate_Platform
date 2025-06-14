# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import time
import uuid
from datetime import datetime
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app, logger=True, engineio_logger=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('debate_platform')

# In-memory database
debates = {}
active_debates = {}
connections = {}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/create', methods=['GET', 'POST'])
def create_debate():
    if request.method == 'POST':
        topic = request.form['topic']
        username = request.form['username']
        time_limit = int(request.form.get('time_limit', 10))
        
        debate_id = str(uuid.uuid4())[:8]
        debates[debate_id] = {
            'id': debate_id,
            'topic': topic,
            'creator': username,
            'time_limit': time_limit,
            'participants': [username],
            'status': 'waiting',
            'messages': [],
            'created_at': datetime.now().isoformat(),
            'current_speaker': None,
            'speaker_timer': None,
            'turn_duration': 120
        }
        session['debate_id'] = debate_id
        session['username'] = username
        return redirect(url_for('debate_room', debate_id=debate_id))
    return render_template('create.html')

@app.route('/join', methods=['GET', 'POST'])
def join_debate():
    if request.method == 'POST':
        debate_id = request.form['debate_id']
        username = request.form['username']
        
        if debate_id in debates:
            if debates[debate_id]['status'] == 'waiting' and len(debates[debate_id]['participants']) < 2:
                debates[debate_id]['participants'].append(username)
                debates[debate_id]['status'] = 'active'
                debates[debate_id]['current_speaker'] = debates[debate_id]['creator']
                debates[debate_id]['speaker_timer'] = time.time()
                active_debates[debate_id] = time.time()
                
                session['debate_id'] = debate_id
                session['username'] = username
                return redirect(url_for('debate_room', debate_id=debate_id))
            return render_template('join.html', error="Debate is already active or full")
        return render_template('join.html', error="Invalid debate ID")
    return render_template('join.html')

@app.route('/debate/<debate_id>')
def debate_room(debate_id):
    if debate_id not in debates:
        return redirect(url_for('home'))
    
    username = session.get('username', 'Guest')
    
    debate = debates[debate_id]
    is_creator = (username == debate['creator'])
    
    # Determine the other participant
    other_participant = None
    if len(debate['participants']) > 1:
        other_participant = [p for p in debate['participants'] if p != username][0]
    
    return render_template('debate_room.html', 
                          debate=debate,
                          username=username,
                          is_creator=is_creator,
                          other_participant=other_participant)

@app.route('/save/<debate_id>')
def save_debate(debate_id):
    if debate_id in debates:
        filename = f"debate_{debate_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(debates[debate_id], f, indent=2)
        return jsonify({"status": "success", "filename": filename})
    return jsonify({"status": "error"})

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")
    if request.sid in connections:
        debate_id = connections[request.sid]['debate_id']
        username = connections[request.sid]['username']
        del connections[request.sid]
        leave_room(debate_id)
        emit('message', {
            'user': 'System',
            'text': f'{username} has left the debate',
            'timestamp': datetime.now().strftime('%H:%M:%S')
        }, room=debate_id)

@socketio.on('join')
def on_join(data):
    debate_id = data['debate_id']
    username = data['username']
    
    join_room(debate_id)
    connections[request.sid] = {
        'debate_id': debate_id,
        'username': username
    }
    
    logger.info(f"{username} joined debate {debate_id}")
    
    emit('message', {
        'user': 'System',
        'text': f'{username} joined the debate',
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }, room=debate_id)
    
    # Send current state
    if debate_id in debates:
        time_left = max(0, debates[debate_id]['turn_duration'] - (time.time() - debates[debate_id]['speaker_timer']))
        emit('debate_state', {
            'current_speaker': debates[debate_id]['current_speaker'],
            'speaker_time_left': time_left
        }, room=request.sid)

@socketio.on('request_speak')
def handle_request_speak(data):
    debate_id = data['debate_id']
    username = data['username']
    
    if debate_id in debates:
        logger.info(f"{username} requested to speak in debate {debate_id}")
        emit('message', {
            'user': 'System',
            'text': f'{username} has requested to speak',
            'timestamp': datetime.now().strftime('%H:%M:%S')
        }, room=debate_id)
        
        # Switch speaker if it's not the same person
        if debates[debate_id]['current_speaker'] != username:
            debates[debate_id]['current_speaker'] = username
            debates[debate_id]['speaker_timer'] = time.time()
            emit('set_speaker', {'speaker': username}, room=debate_id)

@socketio.on('send_message')
def handle_message(data):
    debate_id = data['debate_id']
    message = {
        'user': data['username'],
        'text': data['text'],
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }
    
    if debate_id in debates:
        debates[debate_id]['messages'].append(message)
        emit('message', message, room=debate_id)

@socketio.on('end_turn')
def handle_end_turn(data):
    debate_id = data['debate_id']
    username = data['username']
    
    if debate_id in debates and debates[debate_id]['current_speaker'] == username:
        # Switch to the other participant
        other_participant = [p for p in debates[debate_id]['participants'] if p != username][0]
        
        debates[debate_id]['current_speaker'] = other_participant
        debates[debate_id]['speaker_timer'] = time.time()
        
        logger.info(f"Turn ended by {username}, next speaker: {other_participant}")
        
        emit('message', {
            'user': 'System',
            'text': f'{username} has ended their turn. {other_participant}\'s turn now.',
            'timestamp': datetime.now().strftime('%H:%M:%S')
        }, room=debate_id)
        
        emit('set_speaker', {'speaker': other_participant}, room=debate_id)

@socketio.on('end_debate')
def end_debate(data):
    debate_id = data['debate_id']
    username = data['username']
    
    if debate_id in debates:
        debates[debate_id]['status'] = 'ended'
        debates[debate_id]['ended_at'] = datetime.now().isoformat()
        emit('debate_ended', {'by': username}, room=debate_id)
        
        # Save transcript
        save_debate(debate_id)

@socketio.on('signal')
def handle_signal(data):
    debate_id = data['debate_id']
    target_sid = data['target_sid']
    signal = data['signal']
    
    # Relay the signaling data to the specific client
    emit('signal', {
        'signal': signal,
        'caller_sid': request.sid
    }, room=target_sid)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0')