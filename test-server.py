import socket
def create_labview_compatible_server(instrument, host='localhost', port=5555):
    """Create a server specifically designed for LabVIEW VISA"""
    
    def handle_labview_client(client_socket, address):
        """Handle LabVIEW client with proper VISA protocol"""
        print(f"LabVIEW client connected from {address}")
        
        try:
            # Configure socket for LabVIEW
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            
            while True:
                try:
                    # LabVIEW sends commands terminated with \n
                    data = client_socket.recv(1024).decode('utf-8')
                    if not data:
                        break
                    
                    # Clean the command
                    command = data.strip()
                    if not command:
                        continue
                        
                    print(f"Command: '{command}'")
                    
                    # Process command
                    response = instrument.process_command(command)
                    
                    if response:
                        # Send response exactly as LabVIEW expects
                        response_data = response.encode('utf-8')
                        client_socket.sendall(response_data)
                        print(f"Response: '{response}'")
                    
                except socket.error:
                    break
                except Exception as e:
                    print(f"Error: {e}")
                    break
                    
        finally:
            client_socket.close()
            print(f"Client {address} disconnected")
    
    # Create server
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    
    print(f"LabVIEW SCPI Server running on {host}:{port}")
    print(f"VISA Resource: TCPIP0::{host}::{port}::INSTR")
    
    try:
        while True:
            client_socket, address = server_socket.accept()
            # Handle each client in a separate thread
            client_thread = threading.Thread(
                target=handle_labview_client,
                args=(client_socket, address),
                daemon=True
            )
            client_thread.start()
            
    except KeyboardInterrupt:
        print("\nServer stopping...")
    finally:
        server_socket.close()

# Test it with a simple instrument
class SimpleInstrument:
    def __init__(self, name):
        self.name = name
    
    def process_command(self, command):
        cmd = command.upper().strip()
        if cmd == '*IDN?':
            return f"Test,{self.name},12345,1.0"
        elif cmd == 'MEAS:VOLT:DC?':
            return "1.234567E+00"
        elif cmd == '*RST':
            return ""  # No response for reset
        else:
            return f"ERROR: Unknown command '{command}'"

# Run the test server
if __name__ == "__main__":
    instrument = SimpleInstrument("TestDMM")
    create_labview_compatible_server(instrument, 'localhost', 5555)