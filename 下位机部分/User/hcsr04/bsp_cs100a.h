#ifndef __BSP_GENERALTIME_H
#define __BSP_GENERALTIME_H

#include "stm32f10x.h"

// 定时器预分频
#define GENERAL_TIM_PRESCALER               71

// 定时器周期
#define GENERAL_TIM_PERIOD                  0xFFFF

/************通用定时器TIM参数定义，只限TIM2、3、4、5************/
// 当使用不同的定时器的时候，对应的GPIO是不一样的，这点要注意

#define GENERAL_TIMx                        TIM2
#define GENERAL_TIM_APBxClock_FUN           RCC_APB1PeriphClockCmd
#define GENERAL_TIM_CLK                     RCC_APB1Periph_TIM2
#define GENERAL_TIM_CHANNELx                TIM_Channel_2
#define GENERAL_TIM_IT_CCx                  TIM_IT_CC2


#define GENERAL_TIM_IRQn                    TIM2_IRQn
#define GENERAL_TIM_IRQHANDLER              TIM2_IRQHandler

#define ECHO_RCC_GPIO_CLK                   RCC_APB2Periph_GPIOB
#define ECHO_GPIO_PIN                       GPIO_Pin_3
#define ECHO_GPIO_PORT                      GPIOB


#define TRIG_RCC_GPIO_CLK                   RCC_APB2Periph_GPIOA
#define TRIG_GPIO_PIN                       GPIO_Pin_15
#define TRIG_GPIO_PORT                      GPIOA


//----------------------------------------------------------------
// 获取捕获寄存器值函数宏定义
#define            GENERAL_TIM_GetCapturex_FUN                 TIM_GetCapture2
// 捕获信号极性函数宏定义
#define            GENERAL_TIM_OCxPolarityConfig_FUN           TIM_OC2PolarityConfig

// 测量的起始边沿
#define            GENERAL_TIM_STRAT_ICPolarity        TIM_ICPolarity_Rising 
// 测量的结束边沿
#define            GENERAL_TIM_END_ICPolarity          TIM_ICPolarity_Falling 


//----------------------------------------------------------------
// 定时器输入捕获用户自定义变量结构体声明
typedef struct              
{   
	uint8_t   ucFinishFlag;   // 捕获结束标志位
	uint8_t   ucStartFlag;    // 捕获开始标志位
	uint16_t  usCtr;          // 捕获寄存器的值
	uint16_t  usPeriod;       // 自动重装载寄存器更新标志 
}STRUCT_CAPTURE; 

//----------------------------------------------------------------
extern STRUCT_CAPTURE TIM_ICUserValueStructure;
//----------------------------------------------------------------

/**************************函数声明********************************/
void CS100A_TRIG(void);
void CS100A_Init(void);


#endif	/* __BSP_GENERALTIME_H */


