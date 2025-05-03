import gnupg

def decrypt_file(gpg_passphrase, encrypted_path='encrypted_data.json.gpg', output_path='decrypted_data.json'):
    gpg = gnupg.GPG()
    with open(encrypted_path, 'rb') as f:
        decrypted = gpg.decrypt_file(f, passphrase=gpg_passphrase, output=output_path)
        if not decrypted.ok:
            raise Exception(f"GPG Decryption Failed: {decrypted.stderr}")
    return output_path

