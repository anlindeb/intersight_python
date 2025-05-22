import csv
import logging
import traceback
import intersight
import intersight.api.compute_api
import intersight.api.equipment_api # Required for fetching chassis information
import credentials # Assuming this module handles API client setup and CLI arguments
import argparse # For argument parsing

# Standard logging format
FORMAT = '%(asctime)-15s [%(levelname)s] [%(filename)s:%(lineno)s] %(message)s'
logging.basicConfig(format=FORMAT, level=logging.INFO) # Set to INFO for production, DEBUG for verbose
logger = logging.getLogger('openapi')

# --- Configuration: Chassis Model to Slot Count Mapping ---
# This dictionary maps known chassis models to their typical number of blade slots.
CHASSIS_SLOT_COUNTS = {
    "UCSB-5108-AC2": 8,
    "UCSB-5108-DC2": 8,
    "UCSX-9508": 8,
    # Add other chassis models and their respective blade slot counts here.
}
DEFAULT_SLOT_COUNT = 8  # Fallback slot count.

def main():
    if isinstance(credentials.Parser, argparse.ArgumentParser):
        parser = credentials.Parser
    else:
        parser = credentials.Parser() 

    parser.description = 'Intersight script to get blade slot information, including models and serial numbers, using chassis OperState for populated slots.'

    try:
        parser.add_argument('--csv_file', required=True, help='Path to the CSV file for output.')
    except argparse.ArgumentError:
        logger.info("'--csv_file' argument already defined by credentials.Parser or a parent parser.")

    # Create Intersight API client instance
    client = credentials.config_credentials(parser) 

    try:
        args = parser.parse_args()
    except SystemExit as e:
        logger.error(f"Argument parsing failed. Ensure all required arguments are provided. Error: {e}")
        return

    try:
        # Initialize API instances
        compute_api_instance = intersight.api.compute_api.ComputeApi(client)
        equipment_api_instance = intersight.api.equipment_api.EquipmentApi(client)

        with open(args.csv_file, 'w', newline='') as csvfile:
            # Added ChassisSerial and BladeSerial to fieldnames
            fieldnames = ['Chassis', 'ChassisModel', 'ChassisSerial', 'Slot', 'BladeModel', 'BladeSerial', 'OperState']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            logger.info("Fetching chassis list from Intersight...")
            # Added Serial to the select parameter for chassis
            all_chassis_response = equipment_api_instance.get_equipment_chassis_list(
                select='Moid,Name,Model,OperState,Serial' 
            )

            if not all_chassis_response or not hasattr(all_chassis_response, 'results') or not all_chassis_response.results:
                logger.info("No chassis found in Intersight or unexpected API response format.")
                return

            logger.info(f"Found {len(all_chassis_response.results)} chassis. Processing each...")

            for chassis in all_chassis_response.results:
                chassis_name = chassis.name if hasattr(chassis, 'name') and chassis.name else "UnknownChassis"
                chassis_moid = chassis.moid if hasattr(chassis, 'moid') else "UnknownMoid"
                chassis_model_str = chassis.model if hasattr(chassis, 'model') and chassis.model else "UNKNOWN_MODEL"
                # Get chassis serial number
                chassis_serial_str = chassis.serial if hasattr(chassis, 'serial') and chassis.serial else "UnknownSerial"


                reported_chassis_oper_state = "UnknownChassisState" 
                if hasattr(chassis, 'oper_state'):
                    chassis_state_val = chassis.oper_state 
                    if isinstance(chassis_state_val, str) and chassis_state_val.strip():
                        reported_chassis_oper_state = chassis_state_val.strip()
                    elif isinstance(chassis_state_val, str) and not chassis_state_val.strip():
                        reported_chassis_oper_state = "operable" 
                        logger.debug(f"Chassis '{chassis_name}' (Moid: {chassis_moid}) API OperState was '{chassis_state_val}', interpreted as 'operable'.")
                    elif chassis_state_val is None:
                        reported_chassis_oper_state = "NotReported (ChassisState_is_None)"
                else:
                    logger.warning(f"Chassis '{chassis_name}' (Moid: {chassis_moid}) is missing OperState attribute.")

                total_slots = CHASSIS_SLOT_COUNTS.get(chassis_model_str, DEFAULT_SLOT_COUNT)
                
                if chassis_model_str == "UNKNOWN_MODEL":
                    logger.warning(f"Chassis model is unknown for '{chassis_name}' (Moid: {chassis_moid}). Defaulting to {total_slots} slots. Please verify.")
                elif chassis_model_str not in CHASSIS_SLOT_COUNTS:
                    logger.warning(f"Chassis model '{chassis_model_str}' for '{chassis_name}' (Moid: {chassis_moid}) is not in CHASSIS_SLOT_COUNTS map. Defaulting to {total_slots} slots. Consider updating the map.")

                logger.info(f"Processing Chassis: '{chassis_name}', Model: '{chassis_model_str}', Serial: '{chassis_serial_str}', Expected Slots: {total_slots}, Reported OperState for its blades: '{reported_chassis_oper_state}'")

                # Fetch blades, including their Model and Serial
                blades_in_chassis_response = compute_api_instance.get_compute_blade_list(
                    filter=f"EquipmentChassis.Moid eq '{chassis_moid}'",
                    select='SlotId,Moid,Model,Serial'  # Added Serial to select for blades
                )

                # Store details of populated slots: slot_id -> {'model': blade_model, 'serial': blade_serial}
                populated_slots_details = {} 
                if blades_in_chassis_response and hasattr(blades_in_chassis_response, 'results') and blades_in_chassis_response.results:
                    logger.debug(f"Found {len(blades_in_chassis_response.results)} blades in chassis '{chassis_name}'.")
                    for blade in blades_in_chassis_response.results:
                        blade_debug_id = blade.moid if hasattr(blade, 'moid') else 'N/A'
                        blade_model_str = "UnknownBladeModel" 
                        if hasattr(blade, 'model') and blade.model:
                            blade_model_str = blade.model
                        
                        blade_serial_str = "UnknownBladeSerial"
                        if hasattr(blade, 'serial') and blade.serial:
                            blade_serial_str = blade.serial
                        
                        if hasattr(blade, 'slot_id') and blade.slot_id is not None:
                            try:
                                slot_id = int(blade.slot_id)
                                populated_slots_details[slot_id] = {
                                    'model': blade_model_str,
                                    'serial': blade_serial_str
                                }
                                logger.debug(f"  Chassis '{chassis_name}': Blade (Moid: {blade_debug_id}, Model: {blade_model_str}, Serial: {blade_serial_str}) found in Slot {slot_id}.")
                            except ValueError:
                                logger.warning(f"Could not parse SlotId '{blade.slot_id}' as an integer for blade (Moid: {blade_debug_id}) in chassis '{chassis_name}'. Skipping this blade for population check.")
                        else:
                            logger.warning(f"Blade (Moid: {blade_debug_id}) in chassis '{chassis_name}' is missing SlotId information or SlotId is null. Skipping for population check.")
                else:
                    logger.debug(f"No blades found via API query for chassis '{chassis_name}' or unexpected API response. All slots will be marked as Empty.")

                for slot_num in range(1, total_slots + 1): 
                    final_oper_state_for_csv = "Empty" 
                    current_blade_model = "" 
                    current_blade_serial = "" # Default to empty string for BladeSerial if slot is empty

                    if slot_num in populated_slots_details:
                        final_oper_state_for_csv = reported_chassis_oper_state
                        current_blade_model = populated_slots_details[slot_num]['model'] 
                        current_blade_serial = populated_slots_details[slot_num]['serial']
                    
                    writer.writerow({
                        'Chassis': chassis_name,
                        'ChassisModel': chassis_model_str,
                        'ChassisSerial': chassis_serial_str, # Added chassis serial to CSV
                        'Slot': slot_num,
                        'BladeModel': current_blade_model,
                        'BladeSerial': current_blade_serial, # Added blade serial to CSV
                        'OperState': final_oper_state_for_csv
                    })
                logger.info(f"Finished writing slot information for chassis '{chassis_name}'.")

            logger.info(f"Successfully wrote blade slot information to '{args.csv_file}'")

    except intersight.OpenApiException as e:
        error_details = f"Status: {e.status}, Reason: {e.reason}"
        if e.body: error_details += f", Body: {e.body[:500]}..." 
        if e.headers: error_details += f", Headers: {e.headers}"
        logger.error(f"Intersight API Exception occurred: {error_details}")
        traceback.print_exc()
    except FileNotFoundError:
        logger.error(f"Error: The specified CSV file path '{args.csv_file}' was not found or is invalid.")
        traceback.print_exc()
    except PermissionError:
        logger.error(f"Error: Insufficient permissions to write to '{args.csv_file}'.")
        traceback.print_exc()
    except AttributeError as e:
        logger.error(f"An AttributeError occurred: {e}. This might be due to unexpected API response structure or an issue with the credentials module setup.")
        traceback.print_exc()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()