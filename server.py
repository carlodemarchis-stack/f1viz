import http.server, socketserver, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT=8790
socketserver.TCPServer.allow_reuse_address=True
with socketserver.TCPServer(("",PORT), http.server.SimpleHTTPRequestHandler) as h:
    print(f"http://localhost:{PORT}"); h.serve_forever()
