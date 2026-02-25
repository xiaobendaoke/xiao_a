import socket, time

HOST="192.168.7.1"
PORT=9000

s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST,PORT))

print("recv1:", s.recv(1024).decode(errors="ignore"))

s.sendall(b"PING\n")
time.sleep(0.1)
print("recv2:", s.recv(1024).decode(errors="ignore"))

s.close()
