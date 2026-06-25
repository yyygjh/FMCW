/**
  ******************************************************************************
  * @file    Project/STM32F10x_StdPeriph_Template/stm32f10x_it.c 
  * @author  MCD Application Team
  * @version V3.5.0
  * @date    08-April-2011
  * @brief   Main Interrupt Service Routines.
  *          This file provides template for all exceptions handler and 
  *          peripherals interrupt service routine.
  ******************************************************************************
  * @attention
  *
  * THE PRESENT FIRMWARE WHICH IS FOR GUIDANCE ONLY AIMS AT PROVIDING CUSTOMERS
  * WITH CODING INFORMATION REGARDING THEIR PRODUCTS IN ORDER FOR THEM TO SAVE
  * TIME. AS A RESULT, STMICROELECTRONICS SHALL NOT BE HELD LIABLE FOR ANY
  * DIRECT, INDIRECT OR CONSEQUENTI
  
  AL DAMAGES WITH RESPECT TO ANY CLAIMS ARISING
  * FROM THE CONTENT OF SUCH FIRMWARE AND/OR THE USE MADE BY CUSTOMERS OF THE
  * CODING INFORMATION CONTAINED HEREIN IN CONNECTION WITH THEIR PRODUCTS.
  *
  * <h2><center>&copy; COPYRIGHT 2011 STMicroelectronics</center></h2>
  ******************************************************************************
  */

/* Includes ------------------------------------------------------------------*/
#include "stm32f10x_it.h"
#include <stdio.h>
#include <string.h> 
#include "bsp_SysTick.h"
#include "bsp_esp8266.h"
#include "bsp_esp8266_test.h"
#include "bsp_usart1.h"
#include "bsp_dht11.h"
#include "bsp_cs100a.h"   // 超声波官方头文件

/******************************************************************************/
/*            Cortex-M3 Processor Exceptions Handlers                         */
/******************************************************************************/

void NMI_Handler(void)
{
}

void HardFault_Handler(void)
{
  while (1) {}
}

void MemManage_Handler(void)
{
  while (1) {}
}

void BusFault_Handler(void)
{
  while (1) {}
}

void UsageFault_Handler(void)
{
  while (1) {}
}

void SVC_Handler(void)
{
}

void DebugMon_Handler(void)
{
}

void PendSV_Handler(void)
{
}

// ====================== 合并 SysTick 中断（WiFi + 超声波共用） ======================
void SysTick_Handler(void)
{
    TimingDelay_Decrement();   // 野火官方库需要
}

/******************************************************************************/
/*                         你的 WiFi 串口中断 (保留不动)                         */
/******************************************************************************/
void DEBUG_USART_IRQHandler(void)
{

}

void macESP8266_USART_INT_FUN ( void )
{	
	uint8_t ucCh;
	
	if ( USART_GetITStatus ( macESP8266_USARTx, USART_IT_RXNE ) != RESET )
	{
		ucCh  = USART_ReceiveData( macESP8266_USARTx );
		
		if ( strEsp8266_Fram_Record .InfBit .FramLength < ( RX_BUF_MAX_LEN - 1 ) ) 
			strEsp8266_Fram_Record .Data_RX_BUF [ strEsp8266_Fram_Record .InfBit .FramLength ++ ]  = ucCh;
	}
	 	 
	if ( USART_GetITStatus( macESP8266_USARTx, USART_IT_IDLE ) == SET )
	{
        strEsp8266_Fram_Record .InfBit .FramFinishFlag = 1;
		ucCh = USART_ReceiveData( macESP8266_USARTx );
  }	
}

/******************************************************************************/
/*                     野火官方 HC-SR04 定时器中断 (完整加入)                    */
/******************************************************************************/
void GENERAL_TIM_IRQHANDLER(void)
{
	if ( TIM_GetITStatus ( GENERAL_TIMx, TIM_IT_Update) != RESET )               
	{	
		TIM_ICUserValueStructure.usPeriod ++;		
		TIM_ClearITPendingBit ( GENERAL_TIMx, TIM_FLAG_Update ); 		
	}

	if ( TIM_GetITStatus (GENERAL_TIMx, GENERAL_TIM_IT_CCx ) != RESET)
	{
		if ( TIM_ICUserValueStructure.ucStartFlag == 0 )
		{
			TIM_SetCounter ( GENERAL_TIMx, 0 );
			TIM_ICUserValueStructure.usPeriod = 0;			
			TIM_ICUserValueStructure.usCtr = 0;

			GENERAL_TIM_OCxPolarityConfig_FUN(GENERAL_TIMx, TIM_ICPolarity_Falling);			
			TIM_ICUserValueStructure.ucStartFlag = 1;			
		}
		else 
		{
			TIM_ICUserValueStructure.usCtr = 
			GENERAL_TIM_GetCapturex_FUN (GENERAL_TIMx);

			GENERAL_TIM_OCxPolarityConfig_FUN(GENERAL_TIMx, TIM_ICPolarity_Rising);			
			TIM_ICUserValueStructure.ucStartFlag = 0;			
			TIM_ICUserValueStructure.ucFinishFlag = 1;		
		}

		TIM_ClearITPendingBit (GENERAL_TIMx,GENERAL_TIM_IT_CCx);	    
	}		
}

/******************* (C) COPYRIGHT 2011 STMicroelectronics *****END OF FILE****/
