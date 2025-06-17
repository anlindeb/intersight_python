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
CHASSIS_SLOT_COUNTS = {
    "UCSB-5108-AC2": 8,
    "UCSB-5108-DC2": 8,
    "UCSX-9508": 8,
}
DEFAULT_SLOT_COUNT = 8

# --- Configuration: Two-Slot Blade Models ---
TWO_SLOT_MODELS = {"UCSX-410C-M7", "UCSB-B480-M5"}

def main():
    if isinstance(credentials.Parser, argparse.ArgumentParser):
        parser = credentials.Parser
    else:
        parser = credentials.Parser()

    parser.description = 'Intersight script to get blade slot information, including models, serial numbers, and a summary of slot statuses per chassis model.'

    try:
        parser.add_argument('--csv_file', required=True, help='Path to the CSV file for output.')
    except argparse.ArgumentError:
        logger.info("'--csv_file' argument already defined by credentials.Parser or a parent parser.")

    client = credentials.config_credentials(parser)

    try:
        args = parser.parse_args()
    except SystemExit as e:
        logger.error(f"Argument parsing failed. Ensure all required arguments are provided. Error: {e}")
        return

    model_summary_counts = {}

    try:
        compute_api_instance = intersight.api.compute_api.ComputeApi(client)
        equipment_api_instance = intersight.api.equipment_api.EquipmentApi(client)

        with open(args.csv_file, 'w', newline='') as csvfile:
            fieldnames = ['Chassis', 'ChassisModel', 'ChassisSerial', 'Slot', 'BladeModel', 'BladeSerial', 'OperState']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            logger.info("Fetching chassis list from Intersight...")
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
                chassis_serial_str = chassis.serial if hasattr(chassis, 'serial') and chassis.serial else "UnknownSerial"

                reported_chassis_oper_state = "UnknownChassisState"
                if hasattr(chassis, 'oper_state'):
                    chassis_state_val = chassis.oper_state
                    if isinstance(chassis_state_val, str) and chassis_state_val.strip():
                        reported_chassis_oper_state = chassis_state_val.strip()
                    elif isinstance(chassis_state_val, str) and not chassis_state_val.strip():
                        reported_chassis_oper_state = "operable"
                    elif chassis_state_val is None:
                        reported_chassis_oper_state = "NotReported (ChassisState_is_None)"
                else:
                    logger.warning(f"Chassis '{chassis_name}' is missing OperState attribute.")

                total_slots = CHASSIS_SLOT_COUNTS.get(chassis_model_str, DEFAULT_SLOT_COUNT)

                logger.info(f"Processing Chassis: '{chassis_name}', Model: '{chassis_model_str}', Serial: '{chassis_serial_str}', Slots: {total_slots}")

                blades_in_chassis_response = compute_api_instance.get_compute_blade_list(
                    filter=f"EquipmentChassis.Moid eq '{chassis_moid}'",
                    select='SlotId,Moid,Model,Serial'
                )

                populated_slots_details = {}
                if blades_in_chassis_response and hasattr(blades_in_chassis_response, 'results') and blades_in_chassis_response.results:
                    for blade in blades_in_chassis_response.results:
                        if hasattr(blade, 'slot_id') and blade.slot_id is not None:
                            try:
                                slot_id = int(blade.slot_id)
                                populated_slots_details[slot_id] = {
                                    'model': getattr(blade, 'model', 'UnknownBladeModel'),
                                    'serial': getattr(blade, 'serial', 'UnknownBladeSerial')
                                }
                            except ValueError:
                                logger.warning(f"Could not parse SlotId '{blade.slot_id}' for a blade in chassis '{chassis_name}'.")

                # For CSV output: expand details to cover all slots occupied by multi-slot blades
                expanded_slots_details = {}
                for slot_id, details in populated_slots_details.items():
                    expanded_slots_details[slot_id] = details
                    if details['model'] in TWO_SLOT_MODELS:
                        next_slot_id = slot_id + 1
                        if next_slot_id <= total_slots:
                            expanded_slots_details[next_slot_id] = details

                # --- MODIFICATION START: Corrected Summary Calculation ---
                model_summary_counts.setdefault(chassis_model_str, {})

                # 1. Count each populated blade once towards its status.
                for details in populated_slots_details.values():
                    status = reported_chassis_oper_state
                    model_summary_counts[chassis_model_str].setdefault(status, 0)
                    model_summary_counts[chassis_model_str][status] += 1

                # 2. Calculate the number of total slots occupied by blades.
                slots_occupied_by_blades = 0
                for details in populated_slots_details.values():
                    slots_occupied_by_blades += 2 if details['model'] in TWO_SLOT_MODELS else 1

                # 3. Add the count of empty slots to the summary.
                num_empty_slots = total_slots - slots_occupied_by_blades
                if num_empty_slots > 0:
                    model_summary_counts[chassis_model_str].setdefault("Empty", 0)
                    model_summary_counts[chassis_model_str]["Empty"] += num_empty_slots
                # --- MODIFICATION END ---

                # Loop to write each slot's status to the CSV (summary is no longer calculated here)
                for slot_num in range(1, total_slots + 1):
                    final_oper_state_for_csv = "Empty"
                    current_blade_model = ""
                    current_blade_serial = ""

                    if slot_num in expanded_slots_details:
                        details = expanded_slots_details[slot_num]
                        final_oper_state_for_csv = reported_chassis_oper_state
                        current_blade_model = details['model']
                        current_blade_serial = details['serial']

                    writer.writerow({
                        'Chassis': chassis_name,
                        'ChassisModel': chassis_model_str,
                        'ChassisSerial': chassis_serial_str,
                        'Slot': slot_num,
                        'BladeModel': current_blade_model,
                        'BladeSerial': current_blade_serial,
                        'OperState': final_oper_state_for_csv
                    })

                logger.info(f"Finished writing slot information for chassis '{chassis_name}'.")

            logger.info(f"Successfully wrote blade slot information to '{args.csv_file}'")

            # --- Append Summary Section (no changes here) ---
            logger.info("Appending summary of slot statuses per chassis model...")
            writer.writerow({})
            writer.writerow({})
            summary_title_row = {field: '' for field in fieldnames}
            summary_title_row['Chassis'] = "--- Summary by Chassis Model ---"
            writer.writerow(summary_title_row)
            summary_header_row = {field: '' for field in fieldnames}
            summary_header_row['Chassis'] = "Chassis Model"
            summary_header_row['ChassisModel'] = "Slot Status"
            summary_header_row['ChassisSerial'] = "Count"
            writer.writerow(summary_header_row)

            for model, status_counts in model_summary_counts.items():
                for status, count in status_counts.items():
                    summary_data_row = {field: '' for field in fieldnames}
                    summary_data_row['Chassis'] = model
                    summary_data_row['ChassisModel'] = status
                    summary_data_row['ChassisSerial'] = count
                    writer.writerow(summary_data_row)
            
            logger.info("Summary appended successfully.")

    except intersight.OpenApiException as e:
        error_details = f"Status: {e.status}, Reason: {e.reason}"
        if e.body: error_details += f", Body: {e.body[:500]}..."
        logger.error(f"Intersight API Exception occurred: {error_details}")
        traceback.print_exc()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
