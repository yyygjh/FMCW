#include "bsp_esp8266_test.h"
#include "bsp_esp8266.h"
#include "stm32f10x_it.h"
#include <stdio.h>  
#include <string.h>  
#include <stdbool.h>
#include "bsp_dht11.h"

// 声明距离变量（从main.c来）
extern float g_current_distance;

#define LED1_ON     GPIO_ResetBits(GPIOA, GPIO_Pin_1)
#define LED1_OFF    GPIO_SetBits(GPIOA, GPIO_Pin_1)
#define LED2_ON     GPIO_ResetBits(GPIOA, GPIO_Pin_2)
#define LED2_OFF    GPIO_SetBits(GPIOA, GPIO_Pin_2)
#define LED3_ON     GPIO_ResetBits(GPIOA, GPIO_Pin_3)
#define LED3_OFF    GPIO_SetBits(GPIOA, GPIO_Pin_3)
#define BUZZER_ON   GPIO_SetBits(GPIOB, GPIO_Pin_0)
#define BUZZER_OFF  GPIO_ResetBits(GPIOB, GPIO_Pin_0)

void delay_ms(uint32_t t) {
    uint32_t i,j;
    for(i=0;i<t;i++)
        for(j=0;j<7200;j++);
}

void LED_GPIO_Config(void) {
    GPIO_InitTypeDef GPIO_InitStruct;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
    GPIO_InitStruct.GPIO_Pin = GPIO_Pin_1|GPIO_Pin_2|GPIO_Pin_3;
    GPIO_InitStruct.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_InitStruct.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStruct);
    LED1_OFF; LED2_OFF; LED3_OFF;
}

static void Buzzer_GPIO_Config(void) {
    GPIO_InitTypeDef GPIO_InitStruct;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);
    GPIO_InitStruct.GPIO_Pin = GPIO_Pin_0;
    GPIO_InitStruct.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_InitStruct.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOB, &GPIO_InitStruct);
    BUZZER_OFF;
}

uint8_t ucId, ucLen;
uint8_t ucLed1Status=0,ucLed2Status=0,ucLed3Status=0,ucBuzzerStatus=0;
char cStr[150] = {0}, cCh;
char *pCh, *pCh1;
DHT11_Data_TypeDef DHT11_Data;

#ifndef BUILTAP_TEST
void ESP8266_StaTcpServer_ConfigTest(void) {
    printf("\r\n配置 ESP8266 ...\r\n");
    macESP8266_CH_ENABLE();
    LED_GPIO_Config();
    Buzzer_GPIO_Config();
    while(!ESP8266_AT_Test());
    while(!ESP8266_Net_Mode_Choose(STA));
    while(!ESP8266_DHCP_CUR());
    while(!ESP8266_CIPSTA(macUser_ESP8266_TcpServer_IP));
    while(!ESP8266_JoinAP(macUser_ESP8266_ApSsid,macUser_ESP8266_ApPwd));
    while(!ESP8266_Enable_MultipleId(ENABLE));
    while(!ESP8266_StartOrShutServer(ENABLE,macUser_ESP8266_TcpServer_Port,macUser_ESP8266_TcpServer_OverTime));
    ESP8266_Inquire_StaIp(cStr,20);
    printf("\nWIFI:%s\nIP:%s\nPORT:%s\n",macUser_ESP8266_ApSsid,cStr,macUser_ESP8266_TcpServer_Port);
    strEsp8266_Fram_Record.InfBit.FramLength=0;
    strEsp8266_Fram_Record.InfBit.FramFinishFlag=0;
    printf("\r\n配置完毕\r\n");
}
#else
void ESP8266_ApTcpServer_ConfigTest(void) {
    printf("\r\n配置 ESP8266 ...\r\n");
    macESP8266_CH_ENABLE();
    LED_GPIO_Config();
    Buzzer_GPIO_Config();
    while(!ESP8266_AT_Test());
    while(!ESP8266_Net_Mode_Choose(AP));
    while(!ESP8266_CIPAP(macUser_ESP8266_TcpServer_IP));
    while(!ESP8266_BuildAP(macUser_ESP8266_BulitApSsid,macUser_ESP8266_BulitApPwd,macUser_ESP8266_BulitApEcn));
    while(!ESP8266_Enable_MultipleId(ENABLE));
    while(!ESP8266_StartOrShutServer(ENABLE,macUser_ESP8266_TcpServer_Port,macUser_ESP8266_TcpServer_OverTime));
    ESP8266_Inquire_ApIp(cStr,20);
    printf("\nWIFI:%s\nIP:%s\nPORT:%s\n",macUser_ESP8266_BulitApSsid,cStr,macUser_ESP8266_TcpServer_Port);
    strEsp8266_Fram_Record.InfBit.FramLength=0;
    strEsp8266_Fram_Record.InfBit.FramFinishFlag=0;
    printf("\r\n配置完毕\r\n");
}
#endif

void ESP8266_CheckRecv_SendDataTest(void) {
    int k;
    if(strEsp8266_Fram_Record.InfBit.FramFinishFlag) {
        USART_ITConfig(macESP8266_USARTx, USART_IT_RXNE, DISABLE);
        strEsp8266_Fram_Record.Data_RX_BUF[strEsp8266_Fram_Record.InfBit.FramLength]='\0';
        printf("ucCh=%s\n",strEsp8266_Fram_Record.Data_RX_BUF);

        if((pCh=strstr(strEsp8266_Fram_Record.Data_RX_BUF,"CMD_LED_"))!=0) {
            cCh=*(pCh+8);
            switch(cCh) {
                case '1': cCh=*(pCh+10); if(cCh=='0'){LED1_OFF;ucLed1Status=0;}else if(cCh=='1'){LED1_ON;ucLed1Status=1;} break;
                case '2': cCh=*(pCh+10); if(cCh=='0'){LED2_OFF;ucLed2Status=0;}else if(cCh=='1'){LED2_ON;ucLed2Status=1;} break;
                case '3': cCh=*(pCh+10); if(cCh=='0'){LED3_OFF;ucLed3Status=0;}else if(cCh=='1'){LED3_ON;ucLed3Status=1;} break;
            }
            sprintf(cStr,"LED1:%d LED2:%d LED3:%d",ucLed1Status,ucLed2Status,ucLed3Status);
        } else if((pCh=strstr(strEsp8266_Fram_Record.Data_RX_BUF,"CMD_BUZZER_"))!=0) {
            cCh=*(pCh+11);
            if(cCh=='1'){BUZZER_ON;ucBuzzerStatus=1;for(k=0;k<3;k++){LED1_ON;delay_ms(150);LED1_OFF;delay_ms(150);}LED1_ON;ucLed1Status=1;}
            if(cCh=='0'){BUZZER_OFF;ucBuzzerStatus=0;LED1_OFF;ucLed1Status=0;}
            sprintf(cStr,"BUZZER:%d",ucBuzzerStatus);
        }

        // ====================== 距离发送（唯一能成功的地方）======================
        else if(strstr(strEsp8266_Fram_Record.Data_RX_BUF,"GET_DIST")!=0) {
            if(g_current_distance>5.6) {
                sprintf(cStr,"DIST:无信号");
            } else {
                sprintf(cStr,"DIST:%.2f m",g_current_distance);
            }
        }

        if((pCh=strstr(strEsp8266_Fram_Record.Data_RX_BUF,"+IPD,"))!=0) {
            ucId=*(pCh+strlen("+IPD,"))-'0';
            ESP8266_SendString(DISABLE,cStr,strlen(cStr),(ENUM_ID_NO_TypeDef)ucId);
        }
        strEsp8266_Fram_Record.InfBit.FramLength=0;
        strEsp8266_Fram_Record.InfBit.FramFinishFlag=0;
        USART_ITConfig(macESP8266_USARTx,USART_IT_RXNE,ENABLE);
    }
    if(ucBuzzerStatus==1) {LED1_ON;delay_ms(100);LED1_OFF;delay_ms(100);}
}
