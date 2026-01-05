from machine import I2C, Pin
import time
import struct

ICM20948_ADDR = 0x68

WHO_AM_I = 0x00
PWR_MGMT_1 = 0x06
PWR_MGMT_2 = 0x07
ACCEL_XOUT_H = 0x2D
GYRO_XOUT_H = 0x33

class ICM20948:
    def __init__(self, i2c, address=ICM20948_ADDR):
        self.i2c = i2c
        self.address = address
        self.init_sensor()
    
    def write_reg(self, reg, value):
        self.i2c.writeto_mem(self.address, reg, bytes([value]))
    
    def read_reg(self, reg, nbytes):
        return self.i2c.readfrom_mem(self.address, reg, nbytes)
    
    def init_sensor(self):
        who_am_i = self.read_reg(WHO_AM_I, 1)[0]
        print(f"WHO_AM_I: 0x{who_am_i:02X} (should be 0xEA)")
        
        self.write_reg(PWR_MGMT_1, 0x01)
        time.sleep(0.1)
        
        self.write_reg(PWR_MGMT_2, 0x00)
        time.sleep(0.1)
    
    def read_accel_raw(self):
        data = self.read_reg(ACCEL_XOUT_H, 6)
        ax = struct.unpack('>h', data[0:2])[0]
        ay = struct.unpack('>h', data[2:4])[0]
        az = struct.unpack('>h', data[4:6])[0]
        return ax, ay, az
    
    def read_gyro_raw(self):
        data = self.read_reg(GYRO_XOUT_H, 6)
        gx = struct.unpack('>h', data[0:2])[0]
        gy = struct.unpack('>h', data[2:4])[0]
        gz = struct.unpack('>h', data[4:6])[0]
        return gx, gy, gz
    
    def read_accel_gyro_converted(self):
        ax_raw, ay_raw, az_raw = self.read_accel_raw()
        gx_raw, gy_raw, gz_raw = self.read_gyro_raw()
        
        ax_g = ax_raw / 16384.0
        ay_g = ay_raw / 16384.0
        az_g = az_raw / 16384.0
        
        gx_dps = gx_raw / 131.0
        gy_dps = gy_raw / 131.0
        gz_dps = gz_raw / 131.0
        
        return ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps

imu_instance = None

def init_imu(sda_pin=4, scl_pin=5):
    global imu_instance
    i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=400000)
    
    print("Scanning I2C bus...")
    devices = i2c.scan()
    print(f"Found devices at: {[hex(d) for d in devices]}")
    
    imu_instance = ICM20948(i2c)
    print("IMU initialized successfully")
    return True

def read_imu_data():
    global imu_instance
    if imu_instance is None:
        raise Exception("IMU not initialized. Call init_imu() first.")
    
    return imu_instance.read_accel_gyro_converted()
