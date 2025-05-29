import gnupg
import io
import json

def decrypt_and_load_json(gpg_passphrase, encrypted_path='encrypted_data.json.gpg'):
    gpg = gnupg.GPG()
    gpg.encoding = 'utf-8'

    with open(encrypted_path, 'rb') as f:
        decrypted = gpg.decrypt_file(
            f,
            passphrase=gpg_passphrase,
            extra_args=["--pinentry-mode", "loopback"]
        )

    if not decrypted.ok:
        raise Exception(f"GPG Decryption Failed: {decrypted.stderr}")
    
    # Return parsed JSON directly from decrypted data
    return json.loads(str(decrypted))


def decrypt_file(gpg_passphrase, encrypted_path='encrypted_data.json.gpg', output_path='decrypted_data.json'):
    gpg = gnupg.GPG()
    with open(encrypted_path, 'rb') as f:
        decrypted = gpg.decrypt_file(f, passphrase=gpg_passphrase, output=output_path)
        if not decrypted.ok:
            raise Exception(f"GPG Decryption Failed: {decrypted.stderr}")
    return output_path

