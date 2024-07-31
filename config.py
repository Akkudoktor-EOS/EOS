from datetime import datetime, timedelta


prediction_hours=48 
strafe=10
db_config = {
    'user': '',
    'password': '!',
    'host': '192.168.1.135',
    'database': 'sensor'
}
    
    
    
def get_start_enddate(prediction_hours=48,startdate=None):
        ############
        # Parameter 
        ############
        if startdate == None:
                date = (datetime.now().date() + timedelta(hours = prediction_hours)).strftime("%Y-%m-%d")
                date_now = datetime.now().strftime("%Y-%m-%d")
        else:
                date = (startdate + timedelta(hours = prediction_hours)).strftime("%Y-%m-%d")
                date_now = startdate.strftime("%Y-%m-%d")
        return date_now,date