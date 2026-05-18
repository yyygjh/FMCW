/**
  ******************************************************************************
  * @file    main.c
  * @author  fire
  * @version V1.0
  * @date    2025-xx-xx
  * @brief   WiFi + 超声波 最终版
  ******************************************************************************
  */ 
 
#include "stm32f10x.h"
#include "bsp_usart1.h"
#include "bsp_SysTick.h"
#include "bsp_esp8266.h"
#include "bsp_esp8266_test.h"
#include "bsp_dht11.h"
#include "./dwt_delay/core_delay.h"
#include <stdio.h>
#include <string.h>

// 超声波
#include "bsp_cs100a.h"

// 全局变量：给 esp8266_test.c 读取距离
float g_current_distance = 0.0f;

void Soft_Delay(__IO uint32_t nCount)
{
    for (; nCount != 0; nCount--);
}

int main(void)
{
    uint32_t time;
    float distance;

    USART1_Config();
    CPU_TS_TmrInit();
    ESP8266_Init();
    DHT11_Init();

    SysTick_Init();
    CS100A_Init();

    printf("\r\nWiFi + 超声波 整合成功！\r\n");

    #ifndef BUILTAP_TEST
        ESP8266_StaTcpServer_ConfigTest();
    #else
        ESP8266_ApTcpServer_ConfigTest();
    #endif

    while(1)
    {
        ESP8266_CheckRecv_SendDataTest();

        CS100A_TRIG();

        if(TIM_ICUserValueStructure.ucFinishFlag == 1)
        {
            time = TIM_ICUserValueStructure.usPeriod * GENERAL_TIM_PERIOD + TIM_ICUserValueStructure.usCtr;
            distance = time * 340 / 2000000.0f;

            // 把距离存到全局变量
            if(distance > 5.6)
                g_current_distance = -1.0f;
            else
                g_current_distance = distance;

            // 串口打印
            if(distance > 5.6)
                printf("无信号/超出范围\r\n");
            else
                printf("距离：%.2f m\r\n", distance);

            // 报警逻辑
            if(distance < 0.3 && distance > 0)
            {
                BUZZER_ON;
                LED1_ON;
            }
            else
            {
                BUZZER_OFF;
                LED1_OFF;
            }

            TIM_ICUserValueStructure.ucFinishFlag = 0;
        }
        Soft_Delay(300000);
    }
}
/*********************************************END OF FILE**********************/