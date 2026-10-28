# Media-Info
Kode ini ini bisa digunakan sebagai media info yang menunjukkan informasi lagu yang anda putar saat ini di lcd oled dengan menggunakan chip esp32

Kebutuhan kode ini

RAM: minimal sekitar 4 KB bebas, dan sebaiknya 8 KB atau lebih. Buffer layar saja 1 KB, ditambah variabel dan String. Dalam kondisi terburuk (judul, artis, dan album panjang sekaligus), String bisa memakai 2-3 KB sementara.
Flash: sekitar 15-25 KB di board AVR. Di ESP32 atau ESP8266 sekitar 250-300 KB, karena sebagian besar adalah framework bawaan, bukan kode ini.
