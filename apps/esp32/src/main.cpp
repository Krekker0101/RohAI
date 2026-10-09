#include <Arduino.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>
#include <esp_idf_version.h>
#include <atomic>
#include "stand_config.h"

enum Phase { ALL_RED, NS_GREEN, NS_YELLOW, EW_GREEN, EW_YELLOW, PED_GREEN };
const char* names[]={"ALL_RED","NS_GREEN","NS_YELLOW","EW_GREEN","EW_YELLOW","PED_GREEN"};
portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
uint32_t bootId, sequence=0, leaseUntil=0, entered=0;
Phase phase=ALL_RED, priorClearance=ALL_RED;
bool latched=true, senseFault=false;
std::atomic<bool> guardReady{false}, mainWatchdogReady{false};
bool watchdogConfigured=false;
String session;
String lastCanonical;
uint16_t outputMask=ALL_RED_MASK, sensedMask=0;

uint32_t crc32(const String& value) {
  uint32_t crc=0xffffffff;
  for(size_t i=0;i<value.length();i++) {
    crc^=(uint8_t)value[i];
    for(int n=0;n<8;n++) crc=(crc>>1)^((crc&1)?0xedb88320:0);
  }
  return ~crc;
}
String hexcrc(const String& s) {char b[9];snprintf(b,sizeof(b),"%08lx",(unsigned long)crc32(s));return String(b);}
uint16_t maskFor(Phase p) {
  uint16_t m=ALL_RED_MASK;
  if(p==PED_GREEN)return (m&~(1<<12))|(1<<13);
  if(p==NS_GREEN||p==NS_YELLOW) {
    m&=~((1<<0)|(1<<3));m|=(p==NS_GREEN)?((1<<2)|(1<<5)):((1<<1)|(1<<4));
  }
  if(p==EW_GREEN||p==EW_YELLOW) {
    m&=~((1<<6)|(1<<9));m|=(p==EW_GREEN)?((1<<8)|(1<<11)):((1<<7)|(1<<10));
  }
  return m;
}
void writeOutputs(uint16_t mask) {
  digitalWrite(OUTPUT_LATCH,LOW);
  shiftOut(OUTPUT_DATA,OUTPUT_CLOCK,MSBFIRST,mask>>8);
  shiftOut(OUTPUT_DATA,OUTPUT_CLOCK,MSBFIRST,mask&255);
  digitalWrite(OUTPUT_LATCH,HIGH); outputMask=mask;
}
uint16_t readSense() {
  digitalWrite(SENSE_LOAD,LOW);delayMicroseconds(2);digitalWrite(SENSE_LOAD,HIGH);
  uint16_t m=0;
  for(int i=0;i<16;i++) {m=(m<<1)|digitalRead(SENSE_DATA);digitalWrite(SENSE_CLOCK,HIGH);digitalWrite(SENSE_CLOCK,LOW);}
  return m&0x3fff;
}
void setPhase(Phase p,uint32_t now) {
  if(phase!=p) {priorClearance=phase;phase=p;entered=now;}
  writeOutputs(maskFor(p));
}
void stopStand(uint32_t now) {latched=true;leaseUntil=0;setPhase(ALL_RED,now);}
bool transition(Phase requested,uint32_t now) {
  if(requested==phase)return true;
  uint32_t age=now-entered;
  if(phase==NS_GREEN)return requested==NS_YELLOW&&age>=MIN_GREEN_MS;
  if(phase==EW_GREEN)return requested==EW_YELLOW&&age>=MIN_GREEN_MS;
  if(phase==PED_GREEN)return requested==ALL_RED&&age>=PED_GREEN_MS;
  if(phase==NS_YELLOW||phase==EW_YELLOW)return requested==ALL_RED&&age>=YELLOW_MS;
  uint32_t clearance=(priorClearance==PED_GREEN)?PED_CLEARANCE_MS:ALL_RED_MS;
  return age>=clearance&&(requested==NS_GREEN||requested==EW_GREEN||requested==PED_GREEN);
}
void guardTask(void*) {
  if(!watchdogConfigured||esp_task_wdt_add(nullptr)!=ESP_OK) {for(;;)vTaskDelay(pdMS_TO_TICKS(100));}
  guardReady=true;
  for(;;) {
    uint32_t now=millis();
    portENTER_CRITICAL(&mux);
    if(digitalRead(ESTOP_PIN)==LOW || (!latched&&(int32_t)(now-leaseUntil)>=0))stopStand(now);
    if(!latched&&(phase==NS_GREEN||phase==EW_GREEN||phase==PED_GREEN)&&now-entered>=MAX_GREEN_MS)stopStand(now);
    sensedMask=readSense();
    if(sensedMask!=outputMask) {senseFault=true;stopStand(now);}
    portEXIT_CRITICAL(&mux);
    esp_task_wdt_reset();vTaskDelay(pdMS_TO_TICKS(5));
  }
}
void reply(JsonDocument& request,bool accepted,const char* reason) {
  StaticJsonDocument<1024> doc;
  doc["protocol"]=1;doc["boot"]=bootId;doc["session"]=request["session"];
  doc["firmware_version"]=FIRMWARE_VERSION;
  doc["command_id"]=request["command_id"];doc["sequence"]=request["sequence"];
  doc["accepted"]=accepted;doc["reason"]=reason;doc["uptime_ms"]=millis();
  doc["device_id"]=DEVICE_ID;doc["domain"]="PHYSICAL_MAKET";
  doc["watchdog_present"]=guardReady&&mainWatchdogReady;
  portENTER_CRITICAL(&mux);
  doc["actual_phase"]=names[phase];doc["output_mask"]=outputMask;doc["sense_mask"]=sensedMask;
  doc["watchdog"]=latched?"LATCHED":"OK";doc["sense_fault"]=senseFault;
  doc["estop"]=digitalRead(ESTOP_PIN)==LOW;
  portEXIT_CRITICAL(&mux);
  doc["min_green_ms"]=MIN_GREEN_MS;doc["max_green_ms"]=MAX_GREEN_MS;
  doc["yellow_ms"]=YELLOW_MS;doc["all_red_ms"]=ALL_RED_MS;
  doc["ped_green_ms"]=PED_GREEN_MS;doc["ped_clearance_ms"]=PED_CLEARANCE_MS;
  String payload;serializeJson(doc,payload);
  StaticJsonDocument<1536> outer;outer["payload"]=payload;outer["crc32"]=hexcrc(payload);
  serializeJson(outer,Serial);Serial.println();
}
void process(const String& line) {
  StaticJsonDocument<1024> d;
  if(deserializeJson(d,line))return;
  const char* fields[]={"protocol","boot","session","sequence","command_id","deadline_ms","ttl_ms","kind","phase"};
  String canonical;
  for(int i=0;i<9;i++) {if(d[fields[i]].isNull())return;if(i)canonical+='|';canonical+=d[fields[i]].as<String>();}
  if(d["crc32"].as<String>()!=hexcrc(canonical)||d["protocol"].as<int>()!=1) {reply(d,false,"CRC_OR_VERSION");return;}
  String s=d["session"].as<String>(),kind=d["kind"].as<String>();
  uint32_t seq=d["sequence"],ttl=d["ttl_ms"],deadline=d["deadline_ms"],now=millis();
  if(s.length()!=32||d["command_id"].as<String>().length()!=32){reply(d,false,"IDENTITY");return;}
  // Autosetup inspection must not establish authority, change outputs or renew a lease.
  if(kind=="IDENTIFY") {reply(d,true,"IDENTITY_READ_ONLY");return;}
  if(kind=="HELLO") {
    portENTER_CRITICAL(&mux);stopStand(now);session=s;sequence=seq;lastCanonical="";portEXIT_CRITICAL(&mux);
    reply(d,true,"HELLO_REARM_REQUIRED");return;
  }
  if(d["boot"].as<uint32_t>()!=bootId||s!=session||seq<sequence){reply(d,false,"BOOT_SESSION_REPLAY");return;}
  int32_t remaining=deadline-now;
  if(ttl<500||ttl>2000||remaining<=0||remaining>(int32_t)ttl+100){reply(d,false,"EXPIRED_TTL");return;}
  if(seq==sequence) {
    // Report current readback; never execute again or extend the original lease.
    if(canonical==lastCanonical&&(kind=="SIGNAL"||kind=="STATUS"))reply(d,true,"DUPLICATE_NO_LEASE_RENEWAL");
    else reply(d,false,"BOOT_SESSION_REPLAY");
    return;
  }
  bool accepted=false;const char* reason="INVALID_COMMAND";
  portENTER_CRITICAL(&mux);
  // Check local lease again inside lock; an expired device cannot be renewed remotely.
  if(!latched&&(int32_t)(now-leaseUntil)>=0)stopStand(now);
  if(kind=="ESTOP") {stopStand(now);accepted=true;reason="STOP";}
  else if(kind=="STATUS") {accepted=true;reason="STATUS_NO_LEASE_RENEWAL";}
  else if(kind=="ARM") {
    if(guardReady&&mainWatchdogReady&&digitalRead(LOCAL_REARM_PIN)==LOW&&digitalRead(ESTOP_PIN)==HIGH&&sensedMask==ALL_RED_MASK) {
      senseFault=false;latched=false;phase=ALL_RED;priorClearance=ALL_RED;entered=now;
      leaseUntil=deadline;accepted=true;reason="ARMED";
    } else reason="LOCAL_REARM_AND_READBACK_REQUIRED";
  } else if(kind=="SIGNAL"&&!latched) {
    int desired=-1;for(int i=0;i<6;i++)if(d["phase"].as<String>()==names[i])desired=i;
    if(desired>=0&&transition((Phase)desired,now)) {
      setPhase((Phase)desired,now);leaseUntil=deadline;accepted=true;reason="SIGNAL";
    } else reason="CLEARANCE_OR_PHASE_REJECTED";
  } else if(latched)reason="LATCHED_OPERATOR_REARM_REQUIRED";
  if(accepted)sequence=seq;
  portEXIT_CRITICAL(&mux);
  if(accepted)lastCanonical=canonical;
  // Let electrical sensing settle before acknowledging; guard task owns sense sampling.
  vTaskDelay(pdMS_TO_TICKS(10));reply(d,accepted,reason);
}
void setup() {
  pinMode(OUTPUT_ENABLE,OUTPUT);digitalWrite(OUTPUT_ENABLE,HIGH);
  for(int pin:{OUTPUT_DATA,OUTPUT_CLOCK,OUTPUT_LATCH,SENSE_CLOCK,SENSE_LOAD})pinMode(pin,OUTPUT);
  pinMode(SENSE_DATA,INPUT);pinMode(ESTOP_PIN,INPUT_PULLUP);pinMode(LOCAL_REARM_PIN,INPUT_PULLUP);
  digitalWrite(SENSE_CLOCK,LOW);digitalWrite(SENSE_LOAD,HIGH);
  writeOutputs(ALL_RED_MASK);digitalWrite(OUTPUT_ENABLE,LOW);
  bootId=esp_random();if(!bootId)bootId=1;entered=millis();Serial.begin(115200);
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t wdt={};wdt.timeout_ms=3000;wdt.trigger_panic=true;
  esp_err_t wdtResult=esp_task_wdt_init(&wdt);
  if(wdtResult==ESP_ERR_INVALID_STATE)wdtResult=esp_task_wdt_reconfigure(&wdt);
  watchdogConfigured=wdtResult==ESP_OK;
#else
  watchdogConfigured=esp_task_wdt_init(3,true)==ESP_OK;
#endif
  xTaskCreatePinnedToCore(guardTask,"output-lease-watchdog",4096,nullptr,3,nullptr,0);
  mainWatchdogReady=watchdogConfigured&&esp_task_wdt_add(nullptr)==ESP_OK;
}
void loop() {
  static String line;static bool discard=false;
  while(Serial.available()) {
    char c=Serial.read();
    if(c=='\n') {if(!discard)process(line);line="";discard=false;}
    else if(line.length()<1024&&!discard)line+=c;
    else {line="";discard=true;}
  }
  esp_task_wdt_reset();delay(1);
}
