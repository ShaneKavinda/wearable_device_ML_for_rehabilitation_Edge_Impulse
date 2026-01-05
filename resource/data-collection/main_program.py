from imu_handler import init_imu, read_imu_data
import time
import os

def display_menu():
    print("\n========== IMU Control Menu ==========")
    print("t - Start reading IMU data (50 samples)")
    print("r - Reset IMU")
    print("1 - Collect Flexion data")
    print("2 - Collect Extension data")
    print("3 - Collect Radial Deviation data")
    print("4 - Collect Ulnar Deviation data")
    print("5 - Collect Pronation data")
    print("6 - Collect Supination data")
    print("q - Quit")
    print("=====================================")
    print("Enter your choice: ", end="")

def read_imu_50_samples():
    print("\nReading 50 IMU samples...")
    print("Sample# | Accel(g): X, Y, Z | Gyro(dps): X, Y, Z")
    print("-" * 60)
    
    for i in range(1, 51):
        ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps = read_imu_data()
        print(f"{i:6d} | Accel: {ax_g:6.3f}, {ay_g:6.3f}, {az_g:6.3f} | Gyro: {gx_dps:6.1f}, {gy_dps:6.1f}, {gz_dps:6.1f}")
        time.sleep(0.05)  # 20Hz sampling rate
    
    print("\nCompleted reading 50 samples")
    input("Press Enter to return to menu...")

def collect_gesture_data(gesture_name):
    print(f"\nPreparing to collect {gesture_name} data...")
    print("You will perform 10 repetitions continuously")
    print("\033[94mEach repetition: rest (1.5s) -> motion (2.0s) -> hold (1.5s) -> return (2.0s)\033[0m")
    print("Only motion phase data will be saved")
    input("\nPress Enter to start collecting...")
    
    # Create main data directory if it doesn't exist
    try:
        os.mkdir("data")
    except OSError:
        pass  # Directory already exists
    
    # Find available folder name
    base_dir_name = gesture_name.lower().replace(' ', '-')
    dir_counter = 0
    dir_name = f"data/{base_dir_name}"
    
    # Check if folder exists, if yes, increment counter
    while True:
        try:
            os.mkdir(dir_name)
            print(f"Created folder: {dir_name}")
            break
        except OSError:
            # Folder exists, try next number
            dir_counter += 1
            dir_name = f"data/{base_dir_name}{dir_counter}"
    
    # Phase timings (total 7s)
    phase_durations = {
        "rest": 1.5,
        "motion": 2.0,
        "hold": 1.5,
        "return": 2.0
    }
    
    phases = ["rest", "motion", "hold", "return"]
    
    # Collect 10 repetitions continuously
    for rep in range(1, 11):
        print(f"\n===== Repetition {rep}/10 =====")
        
        # Generate filename with timestamp
        timestamp = int(time.time() * 1000)
        filename = f"{dir_name}/{gesture_name.lower().replace(' ', '_')}_rep{rep}_{timestamp}.csv"
        
        # Open file and write header
        with open(filename, 'w') as f:
            f.write("timestamp,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z,phase,label,raw_acc_x,raw_acc_y,raw_acc_z,raw_gyro_x,raw_gyro_y,raw_gyro_z\n")
            
            sample_count = 0
            
            # Collect data for each phase
            for phase in phases:
                if phase == "motion":
                    print(f"\n***** CURRENT PHASE: \033[1m\033[94m{phase.upper()}\033[0m({phase_durations[phase]:.2f}s) *****")
                else:
                    print(f"\n***** CURRENT PHASE: {phase.upper()} ({phase_durations[phase]:.2f}s) *****")
                
                start_time = time.time()
                duration = phase_durations[phase]
                
                while time.time() - start_time < duration:
                    # Get current timestamp
                    current_timestamp = int(time.time() * 1000)
                    
                    # Read IMU data
                    ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps = read_imu_data()
                    
                    # Only save data during motion phase
                    if phase == "motion":
                        # Calculate raw values (reverse conversion)
                        raw_ax = int(ax_g * 16384.0)
                        raw_ay = int(ay_g * 16384.0)
                        raw_az = int(az_g * 16384.0)
                        raw_gx = int(gx_dps * 131.0)
                        raw_gy = int(gy_dps * 131.0)
                        raw_gz = int(gz_dps * 131.0)
                        
                        # Check if all values are zero - indicates sensor failure
                        if raw_ax == 0 and raw_ay == 0 and raw_az == 0 and raw_gx == 0 and raw_gy == 0 and raw_gz == 0:
                            print("\n\033[91mERROR: All sensor values are zero! IMU connection lost.\033[0m")
                            print("Stopping data collection...")
                            return
                        
                        # Write data to CSV
                        f.write(f"{current_timestamp},{ax_g:.6f},{ay_g:.6f},{az_g:.6f},{gx_dps:.6f},{gy_dps:.6f},{gz_dps:.6f},{phase},{gesture_name},{raw_ax},{raw_ay},{raw_az},{raw_gx},{raw_gy},{raw_gz}\n")
                        
                        sample_count += 1
                    
                    # Maintain 20Hz sampling rate
                    time.sleep(0.05)
        
        print(f"\nSaved: {filename}")
        print(f"Total samples collected: {sample_count}")
    
    print(f"\nCompleted all 10 repetitions for {gesture_name}!")
    input("Press Enter to return to menu...")

def reset_imu():
    print("\nResetting IMU...")
    try:
        if init_imu():
            print("\033[92mIMU reset successful!\033[0m")
        else:
            print("\033[91mIMU reset failed!\033[0m")
    except Exception as e:
        print(f"\033[91mFailed to reset IMU: {e}\033[0m")
    input("Press Enter to return to menu...")

def main():
    print("Initializing IMU...")
    
    try:
        if init_imu():
            print("IMU initialization successful!")
        else:
            print("IMU initialization failed!")
            return
    except Exception as e:
        print(f"Failed to initialize IMU: {e}")
        return
    
    while True:
        display_menu()
        choice = input().lower()
        
        if choice == 't':
            read_imu_50_samples()
        elif choice == 'r':
            reset_imu()
        elif choice == '1':
            collect_gesture_data("Flexion")
        elif choice == '2':
            collect_gesture_data("Extension")
        elif choice == '3':
            collect_gesture_data("Radial Deviation")
        elif choice == '4':
            collect_gesture_data("Ulnar Deviation")
        elif choice == '5':
            collect_gesture_data("Pronation")
        elif choice == '6':
            collect_gesture_data("Supination")
        elif choice == '7':
            collect_gesture_data("Resting")
        elif choice == 'q':
            print("Exiting...")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
